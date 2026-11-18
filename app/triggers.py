"""Opt-in call queue. A proposed row still needs a person to approve it."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from app.crops import CALL_WINDOW_DAYS
from app.db import rows
from app.demo import DEMO_PHONE, ensure_demo_maize
from app.forecast import apply_harvest_state, snapshot
from app.weather import rain_outlook


def _now() -> datetime:
    return datetime.now(timezone.utc)


def harvest_open(crop: str, sow_date: str, today: date | None = None) -> bool:
    today = today or date.today()
    sown = date.fromisoformat(sow_date)
    start, end = CALL_WINDOW_DAYS[crop]
    age = (today - sown).days
    return start <= age <= end


def rebuild(connection, frame, tomato_model, weather_cache, gates: dict | None = None) -> list[dict]:
    """Replace proposed and suppressed rows so the queue matches the latest rule."""
    connection.execute("DELETE FROM triggers WHERE status IN ('proposed', 'suppressed')")
    farmers = rows(connection, "SELECT * FROM farmers")
    created = _now().isoformat()
    recent_cutoff = (_now() - timedelta(hours=48)).isoformat()
    for farmer in farmers:
        sowing = connection.execute(
            "SELECT * FROM sowings WHERE phone = ? ORDER BY sow_date DESC, id DESC LIMIT 1",
            (farmer["phone"],),
        ).fetchone()
        if sowing is None:
            continue
        sowing = dict(sowing)
        if not harvest_open(sowing["crop"], sowing["sow_date"]):
            continue
        snap = apply_harvest_state(
            snapshot(frame, sowing["crop"], tomato_model, (gates or {}).get(sowing["crop"])),
            bool(sowing.get("harvested")),
        )
        if not snap["available"]:
            continue
        outlook = rain_outlook(weather_cache, farmer["district"] or snap["district"])
        wants_call = snap["action"] in {"sell_now", "sell_nearby"} or snap["shortage"] is True
        if farmer["phone"] == DEMO_PHONE or not farmer["opt_in"] or not wants_call:
            continue
        already = connection.execute(
            """
            SELECT id FROM triggers
            WHERE phone = ? AND crop = ? AND mandi = ? AND status IN ('approved', 'called')
              AND created_at >= ?
            """,
            (farmer["phone"], sowing["crop"], snap["mandi"], recent_cutoff),
        ).fetchone()
        if already:
            continue
        reason = snap["action_reason"]
        if snap["shortage"] is True:
            reason = "Mandi is short on this crop. " + reason
        citations = [snap["citation"], outlook["citation"]]
        status = "proposed"
        suppress = None
        if sowing["in_transit"]:
            status = "suppressed"
            suppress = "in_transit"
        elif outlook["heavy"]:
            status = "suppressed"
            suppress = "rain"
        connection.execute(
            """
            INSERT INTO triggers
                (phone, crop, mandi, reason, citations, status, suppress_reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                farmer["phone"],
                sowing["crop"],
                snap["mandi"],
                reason,
                json.dumps(citations),
                status,
                suppress,
                created,
            ),
        )
    connection.commit()
    ensure_demo_maize(connection)
    return rows(connection, "SELECT * FROM triggers ORDER BY id DESC")


def queue_callback(connection, trigger_id: int, frame, tomato_model, gates: dict | None = None) -> dict:
    """Queue the same farmer again. The new row is already approved and carries the last call."""
    current = connection.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
    if current is None:
        return {"error": "missing"}
    current = dict(current)
    if current["status"] == "proposed":
        return {"error": "proposed"}
    waiting = connection.execute(
        "SELECT * FROM triggers WHERE phone = ? AND status = 'approved' ORDER BY id DESC LIMIT 1",
        (current["phone"],),
    ).fetchone()
    if waiting:
        return {"error": "already", "trigger": dict(waiting)}
    sowing = connection.execute(
        "SELECT * FROM sowings WHERE phone = ? ORDER BY sow_date DESC, id DESC LIMIT 1",
        (current["phone"],),
    ).fetchone()
    sowing = dict(sowing) if sowing else None
    crop = sowing["crop"] if sowing else current["crop"]
    snap = apply_harvest_state(
        snapshot(frame, crop, tomato_model, (gates or {}).get(crop)),
        bool(sowing and sowing.get("harvested")),
    )
    advice = snap["action_reason"] if snap.get("available") else current["reason"]
    summary_row = connection.execute(
        """
        SELECT summary FROM calls
        WHERE phone = ? AND summary IS NOT NULL AND summary != ''
        ORDER BY started_at DESC LIMIT 1
        """,
        (current["phone"],),
    ).fetchone()
    summary = summary_row["summary"] if summary_row and summary_row["summary"] else None
    if not summary and current.get("suppress_reason"):
        summary = f"Held back: {current['suppress_reason']}."
    if not summary and current.get("result"):
        summary = current["result"]
    lead = "This is a follow-up call to the same farmer."
    if summary:
        reason = f"{lead} Earlier call: {summary} Current advice: {advice}"
    else:
        reason = f"{lead} Current advice: {advice}"
    citations = []
    if snap.get("available") and snap.get("citation"):
        citations.append(snap["citation"])
    try:
        prior = json.loads(current["citations"] or "[]")
        if isinstance(prior, list):
            citations.extend(str(item) for item in prior[:2])
    except (TypeError, json.JSONDecodeError):
        pass
    citations.append(f"Follow-up of trigger {current['id']}.")
    connection.execute(
        """
        INSERT INTO triggers
            (phone, crop, mandi, reason, citations, status, suppress_reason, created_at)
        VALUES (?, ?, ?, ?, ?, 'approved', NULL, ?)
        """,
        (
            current["phone"],
            crop,
            snap["mandi"] if snap.get("available") else current["mandi"],
            reason,
            json.dumps(citations),
            _now().isoformat(),
        ),
    )
    connection.commit()
    row = connection.execute("SELECT * FROM triggers WHERE id = last_insert_rowid()").fetchone()
    return {"trigger": dict(row)}


def set_status(connection, trigger_id: int, status: str, suppress_reason: str | None = None, result: str | None = None):
    connection.execute(
        """
        UPDATE triggers
        SET status = ?, suppress_reason = COALESCE(?, suppress_reason), result = COALESCE(?, result)
        WHERE id = ?
        """,
        (status, suppress_reason, result, trigger_id),
    )
    connection.commit()
    row = connection.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
    return dict(row) if row else None
