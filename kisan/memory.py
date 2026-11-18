"""Caller memory in Postgres.

Mandi prices are not stored here. They come from the RaithaLine dashboard.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from kisan.advice import _mentioned
from kisan.reference import CROP_ALIASES, Caller, canonical_place
from kisan.summary import render_summary

logger = logging.getLogger("kisan.exotel")

IST = ZoneInfo("Asia/Kolkata")

NO_MEMORY = "No earlier calls are stored for this number."

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS farmers (
        phone TEXT PRIMARY KEY,
        name TEXT,
        district TEXT,
        crop TEXT,
        sown_on TEXT,
        updated_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS calls (
        id BIGSERIAL PRIMARY KEY,
        phone TEXT NOT NULL,
        summary TEXT NOT NULL,
        questions TEXT NOT NULL,
        answers TEXT NOT NULL,
        language TEXT,
        started_at TIMESTAMPTZ NOT NULL,
        ended_at TIMESTAMPTZ NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS calls_phone_idx ON calls (phone, id DESC)",
)


def now_ist() -> datetime:
    return datetime.now(IST)


def database_url() -> str:
    raw = os.environ.get("DATABASE_URL", "").strip()
    if raw.startswith("postgres://"):
        return "postgresql://" + raw[len("postgres://") :]
    return raw


def format_memory(phone: str, farmer: dict | None, calls: list[dict]) -> str:
    """Text the model can read. One number never includes another number's calls."""
    if not phone:
        return "The caller number is unknown, so earlier calls cannot be recalled."
    lines = [f"Caller number {phone}."]
    if not farmer and not calls:
        lines.append("This is the first stored call from this number.")
        return "\n".join(lines)
    if farmer:
        if farmer.get("name"):
            lines.append(f"Name: {farmer['name']}.")
        if farmer.get("district"):
            lines.append(f"Known place: {farmer['district']}.")
        if farmer.get("crop"):
            lines.append(f"Known crop: {farmer['crop']}.")
        if farmer.get("sown_on"):
            lines.append(f"Sown on: {farmer['sown_on']}.")
    if calls:
        lines.append("Earlier calls, newest first:")
        for call in calls:
            stamp = _stamp(call.get("ended_at") or call.get("started_at"))
            summary = " ".join(str(call.get("summary") or "").split())
            lines.append(f"- {stamp}: {summary}".rstrip())
    return "\n".join(lines)


class Memory:
    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = database_url() if dsn is None else database_url_from(dsn)
        self.ready = False

    def init(self) -> None:
        if not self.dsn:
            logger.info("DATABASE_URL is unset, caller memory is off")
            return
        with self._connect() as conn:
            for statement in SCHEMA:
                conn.execute(statement)
        self.ready = True
        logger.info("caller memory ready")

    def load(self, caller: Caller) -> None:
        if not caller.phone or not self.ready:
            caller.memory = NO_MEMORY
            return
        farmer = self.get_farmer(caller.phone)
        calls = self.recent_calls(caller.phone)
        caller.memory = format_memory(caller.phone, farmer, calls)
        if farmer and farmer.get("district") and not caller.place:
            caller.place = str(farmer["district"])

    def get_farmer(self, phone: str) -> dict | None:
        if not phone or not self.ready:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM farmers WHERE phone = %s", (phone,)).fetchone()
        return dict(row) if row else None

    def save_farmer(
        self,
        phone: str,
        *,
        name: str | None = None,
        district: str | None = None,
        crop: str | None = None,
        sown_on: str | None = None,
    ) -> None:
        if not phone or not self.ready:
            return
        record = {
            "phone": phone,
            "name": name or None,
            "district": canonical_place(district) if district else None,
            "crop": crop or None,
            "sown_on": sown_on or None,
            "updated_at": now_ist(),
        }
        if record["district"] == "":
            record["district"] = None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO farmers (phone, name, district, crop, sown_on, updated_at)
                VALUES (%(phone)s, %(name)s, %(district)s, %(crop)s, %(sown_on)s, %(updated_at)s)
                ON CONFLICT (phone) DO UPDATE SET
                    name = COALESCE(EXCLUDED.name, farmers.name),
                    district = COALESCE(EXCLUDED.district, farmers.district),
                    crop = COALESCE(EXCLUDED.crop, farmers.crop),
                    sown_on = COALESCE(EXCLUDED.sown_on, farmers.sown_on),
                    updated_at = EXCLUDED.updated_at
                """,
                record,
            )

    def recent_calls(self, phone: str, limit: int = 5) -> list[dict]:
        if not phone or not self.ready:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, phone, summary, questions, answers, language, started_at, ended_at
                FROM calls
                WHERE phone = %s
                ORDER BY id DESC
                LIMIT %s
                """,
                (phone, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def append_call(
        self,
        phone: str,
        *,
        summary: str,
        questions: str,
        answers: str,
        language: str,
        started_at: datetime,
    ) -> int:
        ended_at = now_ist()
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO calls
                    (phone, summary, questions, answers, language, started_at, ended_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (phone, summary, questions, answers, language, started_at, ended_at),
            ).fetchone()
        return int(row["id"]) if row else 0

    def finish_call(
        self,
        caller: Caller,
        turns: list[tuple[str, str]],
        language: str,
        started_at: datetime,
    ) -> None:
        if not caller.phone or not self.ready:
            return
        spoken = list(turns[1:] if turns and turns[0][0] == "assistant" else turns)
        if not any(role == "user" for role, _text in spoken):
            return
        summary, questions, answers = render_summary(spoken)
        self.save_farmer(
            caller.phone,
            district=caller.place or None,
            crop=_crop_from_turns(spoken),
        )
        self.append_call(
            caller.phone,
            summary=summary,
            questions=questions,
            answers=answers,
            language=language,
            started_at=started_at,
        )
        logger.info("stored caller memory for %s", caller.phone)

    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection]:
        conn = psycopg.connect(self.dsn, connect_timeout=3, row_factory=dict_row)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def database_url_from(raw: str | None) -> str:
    text = (raw or "").strip()
    if text.startswith("postgres://"):
        return "postgresql://" + text[len("postgres://") :]
    return text


def _crop_from_turns(turns: list[tuple[str, str]]) -> str | None:
    for role, text in reversed(turns):
        if role != "user":
            continue
        found = _mentioned(text, CROP_ALIASES)
        if found:
            return found[-1]
    return None


def _stamp(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone(IST).strftime("%Y-%m-%d %H:%M")
    return str(value or "").replace("T", " ")[:16]
