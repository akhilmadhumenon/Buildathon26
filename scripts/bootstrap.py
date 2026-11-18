"""Load Agmarknet files, seed four caller situations, and write the submission notes."""

from __future__ import annotations

import json
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.climate import load as load_climate
from app.climate import refresh as refresh_climate
from app.db import connect, init_db
from app.forecast import fit_tomato, snapshot
from app.prices import load_price_frame, write_prices
from app.reports import market_names, write_climate_report, write_plan_and_refusals, write_price_report
from app.sms import render_sms
from app.triggers import rebuild
from app.weather import load as load_weather
from app.weather import refresh as refresh_weather

DB_PATH = ROOT / "data" / "raitha.db"


def _safe(refresh, load, label: str):
    try:
        print(f"fetching {label}...")
        return refresh()
    except Exception as exc:  # noqa: BLE001 - a cached file can still carry the demo
        print(f"{label} fetch failed ({exc}). Using cache if it exists.")
        return load()


def seed(connection, frame, tomato_model) -> None:
    today = date.today()
    people = [
        {
            "phone": "9845011101",
            "name": "Kolar tomato grower",
            "language": "kn",
            "district": "Kolar",
            "village": "Kolar",
            "lat": 13.136,
            "lon": 78.129,
            "opt_in": 0,
            "crop": "tomato",
            "sow_date": (today - timedelta(days=90)).isoformat(),
            "quantity_qtl": 15,
            "in_transit": 0,
        },
        {
            "phone": "9845011102",
            "name": "Hubballi onion grower",
            "language": "hi",
            "district": "Dharwad",
            "village": "Hubballi",
            "lat": 15.365,
            "lon": 75.124,
            "opt_in": 1,
            "crop": "onion",
            "sow_date": (today - timedelta(days=110)).isoformat(),
            "quantity_qtl": 25,
            "in_transit": 0,
        },
        {
            "phone": "9845011103",
            "name": "Hubballi onion grower in transit",
            "language": "kn",
            "district": "Dharwad",
            "village": "Hubballi",
            "lat": 15.365,
            "lon": 75.124,
            "opt_in": 1,
            "crop": "onion",
            "sow_date": (today - timedelta(days=110)).isoformat(),
            "quantity_qtl": 18,
            "in_transit": 1,
        },
    ]
    now = datetime.now(timezone.utc)
    for person in people:
        connection.execute(
            """
            INSERT INTO farmers (phone, name, language, district, village, lat, lon, opt_in)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                person["phone"],
                person["name"],
                person["language"],
                person["district"],
                person["village"],
                person["lat"],
                person["lon"],
                person["opt_in"],
            ),
        )
        connection.execute(
            """
            INSERT INTO sowings (phone, crop, sow_date, quantity_qtl, in_transit, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                person["phone"],
                person["crop"],
                person["sow_date"],
                person["quantity_qtl"],
                person["in_transit"],
                now.isoformat(),
            ),
        )
    snap = snapshot(frame, "tomato", tomato_model)
    sms = render_sms(snap, "kn") if snap.get("available") else None
    summary = "Earlier call confirmed the same tomato sowing in Kolar. Price and advice were sent by SMS."
    if snap.get("available"):
        summary = (
            f"Earlier call confirmed the same tomato sowing. "
            f"{snap['mandi']} modal was Rs {snap['modal']:.0f} per quintal on {snap['as_of']}."
        )
    connection.execute(
        """
        INSERT INTO calls (id, phone, direction, started_at, ended_at, language, summary, transcript, sms_body)
        VALUES (?, ?, 'inbound', ?, ?, 'kn', ?, ?, ?)
        """,
        (
            uuid.uuid4().hex,
            "9845011101",
            (now - timedelta(days=6)).isoformat(),
            (now - timedelta(days=6)).isoformat(),
            summary,
            "Farmer confirmed the same tomato sowing.",
            sms,
        ),
    )


def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db(DB_PATH)
    frame = load_price_frame()
    print(f"price rows: {len(frame)}")
    if frame.empty:
        raise SystemExit("No Agmarknet files in data/raw. Run scripts/fetch_agmarknet.py first.")
    with connect(DB_PATH) as connection:
        write_prices(connection, frame)
        connection.commit()
    weather = _safe(refresh_weather, load_weather, "Open-Meteo forecast")
    climate = _safe(refresh_climate, load_climate, "Open-Meteo climate normals")
    model = fit_tomato(frame)
    print("tomato model:", model.get("reason"))
    if model.get("metrics"):
        print(json.dumps({k: model["metrics"][k] for k in model["metrics"]}, default=str))
    with connect(DB_PATH) as connection:
        seed(connection, frame, model)
        connection.commit()
        queue = rebuild(connection, frame, model, weather)
    names = market_names(frame)
    print("markets:")
    for crop, market in names.items():
        print(f"  {crop}: {market}")
    print(f"queue rows: {len(queue)}")
    for row in queue:
        print(f"  {row['status']} {row['phone']} {row['crop']} {row.get('suppress_reason') or ''}")
    write_price_report(model, names)
    write_climate_report(climate)
    write_plan_and_refusals(frame, model, weather, climate)
    print("wrote reports and", DB_PATH)


if __name__ == "__main__":
    main()
