"""One demo caller who can be dialed from the maize board."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

DEMO_PHONE = "9372875356"
DEMO_DISTRICT = "Bengaluru Urban"
# Binny Mill is the APMC this desk has inside Bengaluru, so it is the nearest one.
DEMO_MANDI = "Binny Mill (FF&V) Bengaluru APMC"
DEMO_REASON = "Ready to sell maize. The shortage rule is met."


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def revert_demo(connection, trigger_id: int) -> dict | None:
    """Put the demo call back to proposed and clear the yes or no."""
    current = connection.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
    if current is None or current["phone"] != DEMO_PHONE:
        return None
    connection.execute(
        """
        UPDATE triggers
        SET status = 'proposed', suppress_reason = NULL, result = NULL
        WHERE id = ?
        """,
        (trigger_id,),
    )
    connection.commit()
    row = connection.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
    return dict(row) if row else None


def ensure_demo_maize(connection) -> None:
    """Keep this caller opted in, harvested, and proposed. Never held back."""
    sow_date = (date.today() - timedelta(days=100)).isoformat()
    existing = connection.execute("SELECT phone FROM farmers WHERE phone = ?", (DEMO_PHONE,)).fetchone()
    if existing is None:
        connection.execute(
            """
            INSERT INTO farmers (phone, name, language, district, village, lat, lon, opt_in)
            VALUES (?, ?, 'en', ?, ?, 12.972, 77.594, 1)
            """,
            (DEMO_PHONE, "Bengaluru Urban maize grower", DEMO_DISTRICT, DEMO_DISTRICT),
        )
    else:
        connection.execute(
            """
            UPDATE farmers
            SET name = ?, language = 'en', district = ?, village = ?, lat = 12.972, lon = 77.594, opt_in = 1
            WHERE phone = ?
            """,
            ("Bengaluru Urban maize grower", DEMO_DISTRICT, DEMO_DISTRICT, DEMO_PHONE),
        )
    sowing = connection.execute(
        "SELECT id FROM sowings WHERE phone = ? AND crop = 'maize' ORDER BY id DESC LIMIT 1",
        (DEMO_PHONE,),
    ).fetchone()
    if sowing is None:
        connection.execute(
            """
            INSERT INTO sowings (phone, crop, sow_date, quantity_qtl, in_transit, harvested, created_at)
            VALUES (?, 'maize', ?, 20, 0, 1, ?)
            """,
            (DEMO_PHONE, sow_date, _now()),
        )
    else:
        connection.execute(
            """
            UPDATE sowings
            SET sow_date = ?, quantity_qtl = 20, in_transit = 0, harvested = 1
            WHERE id = ?
            """,
            (sow_date, sowing["id"]),
        )
    latest = connection.execute(
        """
        SELECT status FROM triggers
        WHERE phone = ? AND crop = 'maize'
        ORDER BY id DESC LIMIT 1
        """,
        (DEMO_PHONE,),
    ).fetchone()
    if latest is not None and latest["status"] in {"proposed", "approved", "called", "yes", "no"}:
        connection.commit()
        return
    connection.execute(
        "DELETE FROM triggers WHERE phone = ? AND status IN ('proposed', 'suppressed')",
        (DEMO_PHONE,),
    )
    connection.execute(
        """
        INSERT INTO triggers
            (phone, crop, mandi, reason, citations, status, suppress_reason, created_at)
        VALUES (?, 'maize', ?, ?, ?, 'proposed', NULL, ?)
        """,
        (
            DEMO_PHONE,
            DEMO_MANDI,
            DEMO_REASON,
            json.dumps(["Demo row. Shortage rule treated as met. Mandi is the Bengaluru APMC."]),
            _now(),
        ),
    )
    connection.commit()
