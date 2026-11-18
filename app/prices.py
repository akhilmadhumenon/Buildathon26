"""Turn Agmarknet month files into one daily series per crop."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.crops import CROPS

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"


def _parse_date(value: str):
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except (TypeError, ValueError):
            continue
    return None


def _day_record(day: dict) -> dict | None:
    varieties = day.get("data") or []
    modals = []
    weights = []
    mins = []
    maxs = []
    for variety in varieties:
        modal = variety.get("modalPrice")
        if modal is None:
            continue
        modals.append(float(modal))
        weights.append(float(variety.get("arrivals") or 0))
        if variety.get("minimumPrice") is not None:
            mins.append(float(variety["minimumPrice"]))
        if variety.get("maximumPrice") is not None:
            maxs.append(float(variety["maximumPrice"]))
    if not modals:
        return None
    weight_sum = sum(weights)
    if weight_sum > 0:
        modal = sum(weight * price for weight, price in zip(weights, modals)) / weight_sum
    else:
        modal = sum(modals) / len(modals)
    arrivals = day.get("total_arrivals")
    if arrivals is None:
        arrivals = weight_sum
    return {
        "arrival_tonnes": float(arrivals) if arrivals is not None else None,
        "min_price": min(mins) if mins else None,
        "max_price": max(maxs) if maxs else None,
        "modal_price": float(modal),
    }


def iter_market_rows():
    if not RAW.exists():
        return
    for path in sorted(RAW.glob("*_????_??.json")):
        crop = path.name.split("_")[0]
        if crop not in CROPS:
            continue
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        for market in payload.get("markets") or []:
            name = (market.get("marketName") or "").strip()
            if not name:
                continue
            for day in market.get("dates") or []:
                on = _parse_date(day.get("arrivalDate") or "")
                record = _day_record(day)
                if on is None or record is None:
                    continue
                yield {
                    "crop": crop,
                    "market": name,
                    "date": on.isoformat(),
                    **record,
                }


# A mandi needs enough reported days to compare with the same week last year.
MIN_MARKET_DAYS = 180


def _choose_market(crop: str, role: str, frame: pd.DataFrame) -> str | None:
    if frame.empty:
        return None
    counts = frame.groupby(frame["market"].str.lower()).size().to_dict()
    display = {name.lower(): name for name in frame["market"].unique()}
    preferences = CROPS[crop]["markets"] if role == "primary" else CROPS[crop]["nearby_markets"]
    for preference in preferences:
        qualified = [key for key, count in counts.items() if preference in key and count >= MIN_MARKET_DAYS]
        if qualified:
            return display[max(qualified, key=counts.get)]
    if role != "primary" or not counts:
        return None
    fullest = max(counts, key=counts.get)
    if counts[fullest] < 30:
        return None
    return display[fullest]


def load_price_frame() -> pd.DataFrame:
    rows = list(iter_market_rows())
    if not rows:
        return pd.DataFrame(
            columns=["crop", "role", "market", "district", "date", "arrival_tonnes", "min_price", "max_price", "modal_price"]
        )
    frame = pd.DataFrame(rows)
    chosen = []
    for crop, crop_rows in frame.groupby("crop"):
        primary = _choose_market(crop, "primary", crop_rows)
        nearby = _choose_market(crop, "nearby", crop_rows)
        for role, market in (("primary", primary), ("nearby", nearby)):
            if not market:
                continue
            part = crop_rows[crop_rows["market"] == market].copy()
            part["role"] = role
            part["district"] = CROPS[crop]["district"] if role == "primary" else "Bengaluru"
            chosen.append(part)
    if not chosen:
        return pd.DataFrame()
    out = pd.concat(chosen, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"])
    out = out.sort_values(["crop", "role", "date"])
    out = out.drop_duplicates(["crop", "role", "date"], keep="last")
    return out


def read_prices(connection) -> pd.DataFrame:
    frame = pd.read_sql_query("SELECT * FROM prices", connection)
    if not frame.empty:
        frame["date"] = pd.to_datetime(frame["date"])
    return frame


def write_prices(connection, frame: pd.DataFrame) -> None:
    connection.execute("DELETE FROM prices")
    if frame.empty:
        return
    records = []
    for row in frame.itertuples(index=False):
        records.append(
            (
                row.crop,
                row.role,
                row.market,
                row.district,
                pd.Timestamp(row.date).date().isoformat(),
                None if pd.isna(row.arrival_tonnes) else float(row.arrival_tonnes),
                None if pd.isna(row.min_price) else float(row.min_price),
                None if pd.isna(row.max_price) else float(row.max_price),
                None if pd.isna(row.modal_price) else float(row.modal_price),
            )
        )
    connection.executemany(
        """
        INSERT INTO prices
            (crop, role, market, district, date, arrival_tonnes, min_price, max_price, modal_price)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        records,
    )
