"""SQLite store for farmer profiles, mandi prices, and call summaries."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

SCHEMA = """
CREATE TABLE IF NOT EXISTS farmers (
    phone TEXT PRIMARY KEY,
    name TEXT,
    district TEXT,
    crop TEXT,
    sown_on TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL,
    summary TEXT NOT NULL,
    questions TEXT NOT NULL,
    answers TEXT NOT NULL,
    language TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mandi_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    crop TEXT NOT NULL,
    mandi TEXT NOT NULL,
    district TEXT NOT NULL,
    price_per_quintal INTEGER NOT NULL,
    recorded_on TEXT NOT NULL,
    trend TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS crop_guides (
    crop TEXT PRIMARY KEY,
    days_to_harvest INTEGER NOT NULL,
    harvest_notes TEXT NOT NULL
);
"""

CROP_ALIASES = {
    "ragi": "ragi",
    "finger millet": "ragi",
    "tomato": "tomato",
    "tomatoes": "tomato",
    "maize": "maize",
    "corn": "maize",
    "paddy": "paddy",
    "rice": "paddy",
    "onion": "onion",
    "onions": "onion",
    "chilli": "chilli",
    "chilies": "chilli",
    "chilli pepper": "chilli",
    "chili": "chilli",
    "टमाटर": "tomato",
    "रागी": "ragi",
    "मक्का": "maize",
    "धान": "paddy",
    "प्याज": "onion",
    "मिर्च": "chilli",
    "ಟೊಮೇಟೊ": "tomato",
    "ಟೊಮೆಟೊ": "tomato",
    "ಟಮಾಟೊ": "tomato",
    "ಟೊಮಾಟೊ": "tomato",
    "ಟೊಮ್ಯಾಟೊ": "tomato",
    "ರಾಗಿ": "ragi",
    "ಮೆಕ್ಕೆಜೋಳ": "maize",
    "ಭತ್ತ": "paddy",
    "ಈರುಳ್ಳಿ": "onion",
    "ಮೆಣಸಿನಕಾಯಿ": "chilli",
    "banana": "banana",
    "bananas": "banana",
    "mango": "mango",
    "mangoes": "mango",
    "grape": "grapes",
    "grapes": "grapes",
    "केला": "banana",
    "आम": "mango",
    "अंगूर": "grapes",
    "ಬಾಳೆಹಣ್ಣು": "banana",
    "ಮಾವಿನಹಣ್ಣು": "mango",
    "ದ್ರಾಕ್ಷಿ": "grapes",
}

PLACE_ALIASES = {
    "mandya": "Mandya",
    "mysuru": "Mysuru",
    "mysore": "Mysuru",
    "kolar": "Kolar",
    "davangere": "Davangere",
    "davanagere": "Davangere",
    "hubballi": "Hubballi",
    "hubli": "Hubballi",
    "gangavathi": "Gangavathi",
    "gangavati": "Gangavathi",
    "bengaluru": "Bengaluru",
    "bangalore": "Bengaluru",
    "hassan": "Hassan",
    "tumakuru": "Tumakuru",
    "tumkur": "Tumakuru",
    "raichur": "Raichur",
    "ballari": "Ballari",
    "bellary": "Ballari",
    "shivamogga": "Shivamogga",
    "shimoga": "Shivamogga",
    "कोलार": "Kolar",
    "मंड्या": "Mandya",
    "हुबली": "Hubballi",
    "बेंगलुरु": "Bengaluru",
    "ಕೋಲಾರ": "Kolar",
    "ಮಂಡ್ಯ": "Mandya",
    "ಹುಬ್ಬಳ್ಳಿ": "Hubballi",
    "ಬೆಂಗಳೂರು": "Bengaluru",
    "huskur": "Huskur",
    "huskuru": "Huskur",
    "huskhur": "Huskur",
    "ಹುಸ್ಕೂರು": "Huskur",
    "हुसकुर": "Huskur",
}

# Approximate district centres for the demo weather lookup.
DISTRICT_COORDS = {
    "mandya": (12.52, 76.90),
    "mysuru": (12.30, 76.64),
    "kolar": (13.14, 78.13),
    "davangere": (14.47, 75.92),
    "hubballi": (15.36, 75.12),
    "gangavathi": (15.43, 76.53),
    "bengaluru": (12.97, 77.59),
    "huskur": (12.843, 77.689),
    "hassan": (13.01, 76.10),
    "tumakuru": (13.34, 77.10),
    "raichur": (16.21, 77.36),
    "ballari": (15.14, 76.92),
    "shivamogga": (13.93, 75.57),
}


def now_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def normalize_phone(raw: str | None) -> str:
    """Store Indian mobiles as +91 and ten digits so repeat calls match."""
    if not raw:
        return ""
    digits = "".join(char for char in raw if char.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10:
        return "+91" + digits
    cleaned = raw.strip()
    return cleaned


def canonical_crop(value: str | None) -> str:
    key = " ".join((value or "").strip().lower().split())
    return CROP_ALIASES.get(key, key)


def canonical_place(value: str | None) -> str:
    key = " ".join((value or "").strip().lower().split())
    return PLACE_ALIASES.get(key, (value or "").strip())


class Database:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.environ.get("KISAN_DB", "data/kisan.db")

    def connect(self) -> sqlite3.Connection:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def session(self):
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self.session() as conn:
            conn.executescript(SCHEMA)

    def seed_if_empty(self) -> None:
        self.init()
        with self.session() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM mandi_prices").fetchone()["n"]
            if count:
                return
            conn.executemany(
                """
                INSERT INTO mandi_prices
                    (crop, mandi, district, price_per_quintal, recorded_on, trend)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                build_price_rows(),
            )
            conn.executemany(
                "INSERT INTO crop_guides (crop, days_to_harvest, harvest_notes) VALUES (?, ?, ?)",
                SEED_CROPS,
            )

    def get_farmer(self, phone: str) -> dict | None:
        with self.session() as conn:
            row = conn.execute("SELECT * FROM farmers WHERE phone = ?", (phone,)).fetchone()
        return dict(row) if row else None

    def save_farmer(
        self,
        phone: str,
        *,
        name: str | None = None,
        district: str | None = None,
        crop: str | None = None,
        sown_on: str | None = None,
    ) -> dict:
        current = self.get_farmer(phone) or {}
        record = {
            "phone": phone,
            "name": name or current.get("name"),
            "district": canonical_place(district) if district else current.get("district"),
            "crop": canonical_crop(crop) if crop else current.get("crop"),
            "sown_on": sown_on or current.get("sown_on"),
            "updated_at": now_iso(),
        }
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO farmers (phone, name, district, crop, sown_on, updated_at)
                VALUES (:phone, :name, :district, :crop, :sown_on, :updated_at)
                ON CONFLICT(phone) DO UPDATE SET
                    name = excluded.name,
                    district = excluded.district,
                    crop = excluded.crop,
                    sown_on = excluded.sown_on,
                    updated_at = excluded.updated_at
                """,
                record,
            )
        return record

    def recent_calls(self, phone: str, limit: int = 5) -> list[dict]:
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT * FROM calls
                WHERE phone = ?
                ORDER BY id DESC
                LIMIT ?
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
        started_at: str,
    ) -> int:
        ended_at = now_iso()
        with self.session() as conn:
            cursor = conn.execute(
                """
                INSERT INTO calls
                    (phone, summary, questions, answers, language, started_at, ended_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (phone, summary, questions, answers, language, started_at, ended_at),
            )
            return int(cursor.lastrowid)

    def mandi_history(self, crop: str, place: str) -> list[dict]:
        crop_key = canonical_crop(crop)
        place_key = canonical_place(place)
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT * FROM mandi_prices
                WHERE crop = ? AND (mandi = ? OR district = ?)
                ORDER BY recorded_on DESC
                """,
                (crop_key, place_key, place_key),
            ).fetchall()
        return [dict(row) for row in rows]

    def crop_history(self, crop: str) -> list[dict]:
        """Every stored price for a crop, newest first."""
        crop_key = canonical_crop(crop)
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT * FROM mandi_prices
                WHERE crop = ?
                ORDER BY recorded_on DESC, id DESC
                """,
                (crop_key,),
            ).fetchall()
        return [dict(row) for row in rows]

    def reload_prices(self) -> int:
        """Replace mandi prices with the demo series. Farmer profiles and calls stay."""
        self.init()
        rows = build_price_rows()
        with self.session() as conn:
            conn.execute("DELETE FROM mandi_prices")
            conn.executemany(
                """
                INSERT INTO mandi_prices
                    (crop, mandi, district, price_per_quintal, recorded_on, trend)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.executemany(
                """
                INSERT INTO crop_guides (crop, days_to_harvest, harvest_notes)
                VALUES (?, ?, ?)
                ON CONFLICT(crop) DO UPDATE SET
                    days_to_harvest = excluded.days_to_harvest,
                    harvest_notes = excluded.harvest_notes
                """,
                SEED_CROPS,
            )
        return len(rows)

    def latest_prices(self, crop: str = "") -> list[dict]:
        """The newest observed price for each crop and mandi. Forecast days are skipped."""
        today = date.today().isoformat()
        rows = self.crop_history(crop) if crop else self._all_prices()
        seen: set[tuple[str, str]] = set()
        latest = []
        for row in rows:
            if row["recorded_on"] > today:
                continue
            key = (row["crop"], row["mandi"])
            if key in seen:
                continue
            seen.add(key)
            latest.append(row)
        return latest

    def _all_prices(self) -> list[dict]:
        with self.session() as conn:
            rows = conn.execute(
                "SELECT * FROM mandi_prices ORDER BY recorded_on DESC, id DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def known_markets(self) -> list[str]:
        with self.session() as conn:
            rows = conn.execute(
                "SELECT DISTINCT crop, mandi FROM mandi_prices ORDER BY crop, mandi"
            ).fetchall()
        return [f"{row['crop']} at {row['mandi']}" for row in rows]

    def crop_guide(self, crop: str) -> dict | None:
        with self.session() as conn:
            row = conn.execute(
                "SELECT * FROM crop_guides WHERE crop = ?",
                (canonical_crop(crop),),
            ).fetchone()
        return dict(row) if row else None


SEED_CROPS = [
    ("ragi", 120, "Harvest when the earheads turn brown and the grain is hard."),
    ("tomato", 90, "Pick when fruits are firm and starting to colour. Heavy rain splits ripe fruit."),
    ("maize", 110, "Harvest when the cob husk is dry and the grain is hard."),
    ("paddy", 130, "Harvest when most grains are golden and hard. Standing water makes cutting harder."),
    ("onion", 120, "Harvest when the tops fall over and the necks are dry. Rain at lifting causes rot."),
    ("chilli", 150, "Pick red ripe fruits in dry weather. Rain on ripe pods causes spotting."),
    ("banana", 300, "Cut the bunch when the fingers are full and the edges have rounded."),
    ("mango", 120, "Pick when the shoulders fill out and the fruit starts to colour."),
    ("grapes", 150, "Harvest when the berries are fully coloured and sweet."),
]


def build_price_rows(as_of: date | None = None) -> list[tuple]:
    """Eleven days for each demo market: five before today, today, and five forecast days.

    The daily step is chosen so each crop tells a different story.
    Tomato and onion keep falling. Paddy, maize, and chilli keep rising. Ragi barely moves.
    """
    as_of = as_of or date.today()
    specs = (
        ("tomato", "Kolar", "Kolar", 2300, -100),
        ("ragi", "Mandya", "Mandya", 3180, 4),
        ("maize", "Davangere", "Davangere", 1900, 60),
        ("paddy", "Gangavathi", "Raichur", 2170, 30),
        ("onion", "Hubballi", "Hubballi", 2800, -70),
        ("chilli", "Bengaluru", "Bengaluru", 13800, 100),
        ("banana", "Huskur", "Bengaluru", 1600, 40),
        ("mango", "Huskur", "Bengaluru", 7000, -200),
        ("grapes", "Huskur", "Bengaluru", 4200, 0),
    )
    rows: list[tuple] = []
    for crop, mandi, district, start, step in specs:
        direction = "flat" if step == 0 else "up" if step > 0 else "down"
        for offset in range(-5, 6):
            price = start + step * (offset + 5)
            kind = "forecast" if offset > 0 else direction
            day = (as_of + timedelta(days=offset)).isoformat()
            rows.append((crop, mandi, district, int(price), day, kind))
    return rows
