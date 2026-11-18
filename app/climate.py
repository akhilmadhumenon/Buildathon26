"""EcoCrop screen for the ten crops at Kolar and Kalaburagi."""

from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

from app.crops import CLIMATE_DISTRICTS, CROPS, PLACES

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache" / "climate.json"
ECOCROP_PATH = ROOT / "data" / "ecocrop.json"
CTX = ssl._create_unverified_context()
ARCHIVE = (
    "Open-Meteo historical weather API, daily temperature_2m_max, "
    "temperature_2m_min and precipitation_sum, 2015-01-01 to 2024-12-31, Asia/Kolkata."
)
WORLDCLIM_PATH = ROOT / "data" / "cache" / "worldclim.json"
DELTA_NOTE = (
    "Temperature and rainfall shifts are the mean of WorldClim CMIP6 MIROC6 and "
    "MPI-ESM1-2-HR at the 10-arc-minute cell over the district, 2041–2060 minus the "
    "WorldClim 1970–2000 baseline. That temperature delta is added to the Open-Meteo "
    "2015–2024 normal, and rainfall is multiplied by the WorldClim ratio. "
    "The two cells warm by about 0.8°C to 1.2°C, which is milder than a blanket "
    "+1.5°C to +2.5°C slogan. This is two models at one cell, not a full ensemble raster in the app."
)


def eco_table() -> dict[str, dict]:
    rows = json.loads(ECOCROP_PATH.read_text())
    return {row["crop"]: row for row in rows}


def _fetch_archive(name: str, lat: float, lon: float) -> dict:
    query = urllib.parse.urlencode(
        {
            "latitude": lat,
            "longitude": lon,
            "start_date": "2015-01-01",
            "end_date": "2024-12-31",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
            "timezone": "Asia/Kolkata",
        }
    )
    url = f"https://archive-api.open-meteo.com/v1/archive?{query}"
    with urllib.request.urlopen(url, timeout=90, context=CTX) as response:
        payload = json.loads(response.read().decode())
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(payload["daily"]["time"]),
            "tmax": payload["daily"]["temperature_2m_max"],
            "tmin": payload["daily"]["temperature_2m_min"],
            "precip": payload["daily"]["precipitation_sum"],
        }
    )
    frame["tmean"] = (frame["tmax"] + frame["tmin"]) / 2
    frame["year"] = frame["date"].dt.year
    annual = frame.groupby("year").agg(tmean=("tmean", "mean"), precip=("precip", "sum"))
    return {
        "district": name,
        "lat": lat,
        "lon": lon,
        "tmean_c": round(float(annual["tmean"].mean()), 2),
        "precip_mm": round(float(annual["precip"].mean()), 1),
        "years": [int(year) for year in annual.index],
    }


def refresh() -> dict:
    places = {}
    for name in CLIMATE_DISTRICTS:
        lat, lon = PLACES[name]
        places[name] = _fetch_archive(name, lat, lon)
    body = {"source": ARCHIVE, "delta_note": DELTA_NOTE, "places": places}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(body))
    return body


def load() -> dict:
    if CACHE.exists():
        return json.loads(CACHE.read_text())
    return refresh()


def _limb(value: float, lo_abs: float, lo_opt: float, hi_opt: float, hi_abs: float) -> float:
    if lo_opt <= value <= hi_opt:
        return 100.0
    if value <= lo_abs or value >= hi_abs:
        return 0.0
    if value < lo_opt:
        span = lo_opt - lo_abs
        return 0.0 if span == 0 else 100.0 * (value - lo_abs) / span
    span = hi_abs - hi_opt
    return 0.0 if span == 0 else 100.0 * (hi_abs - value) / span


def score_crop(row: dict, tmean: float, precip: float) -> dict:
    temperature = _limb(tmean, row["tmin"], row["topmn"], row["topmx"], row["tmax"])
    rainfall = _limb(precip, row["rmin"], row["ropmn"], row["ropmx"], row["rmax"])
    combined = 0.6 * temperature + 0.4 * rainfall
    return {
        "temperature": round(temperature, 1),
        "rainfall": round(rainfall, 1),
        "score": round(combined, 1),
    }


def worldclim_shifts() -> dict:
    if WORLDCLIM_PATH.exists():
        return json.loads(WORLDCLIM_PATH.read_text())
    return {"places": {}, "source": "WorldClim file is missing."}


def shifts_for(district: str) -> dict:
    """Deterministic WorldClim cell. Temperature is added. Rainfall is a ratio."""
    places = worldclim_shifts().get("places") or {}
    place = places.get(district) or places.get("Kolar") or {}
    moderate = place.get("ssp245") or {"delta_t_c": 0.0, "precip_ratio": 1.0}
    high = place.get("ssp585") or {"delta_t_c": 0.0, "precip_ratio": 1.0}
    return {
        "today": {"delta_t_c": 0.0, "precip_ratio": 1.0},
        "ssp245_2041_2060": moderate,
        "ssp585_2041_2060": high,
    }


def suitability(cache: dict) -> list[dict]:
    table = eco_table()
    cards = []
    for district, place in cache["places"].items():
        shifts = shifts_for(district)
        for crop, limits in table.items():
            scenarios = {}
            for name, shift in shifts.items():
                scenarios[name] = score_crop(
                    limits,
                    place["tmean_c"] + float(shift["delta_t_c"]),
                    place["precip_mm"] * float(shift["precip_ratio"]),
                )
            today = scenarios["today"]["score"]
            high = scenarios["ssp585_2041_2060"]["score"]
            cards.append(
                {
                    "district": district,
                    "crop": crop,
                    "crop_en": CROPS[crop]["en"],
                    "eco_name": limits["eco_name"],
                    "scientific_name": limits["scientific_name"],
                    "tmean_c": place["tmean_c"],
                    "precip_mm": place["precip_mm"],
                    "shifts": {
                        "ssp245_2041_2060": {
                            "delta_t_c": shifts["ssp245_2041_2060"]["delta_t_c"],
                            "precip_ratio": shifts["ssp245_2041_2060"]["precip_ratio"],
                        },
                        "ssp585_2041_2060": {
                            "delta_t_c": shifts["ssp585_2041_2060"]["delta_t_c"],
                            "precip_ratio": shifts["ssp585_2041_2060"]["precip_ratio"],
                        },
                    },
                    "scenarios": scenarios,
                    "change_high": round(high - today, 1),
                }
            )
    return cards


def nearest_screened_district(district: str) -> str:
    if district in CLIMATE_DISTRICTS:
        return district
    if district not in PLACES:
        return "Kolar"
    lat, lon = PLACES[district]
    best = "Kolar"
    best_distance = 10**9
    for name in CLIMATE_DISTRICTS:
        other_lat, other_lon = PLACES[name]
        distance = (lat - other_lat) ** 2 + (lon - other_lon) ** 2
        if distance < best_distance:
            best = name
            best_distance = distance
    return best


def next_sowing_brief(cache: dict, crop: str, district: str | None) -> dict:
    """Spoken at the end of a harvest cycle: this crop against ragi and tur under the 2040 heat screen."""
    screened = nearest_screened_district(district or "Kolar")
    order: list[str] = []
    for name in (crop, "ragi", "tur"):
        if name in CROPS and name not in order:
            order.append(name)
    bits = []
    cards = []
    for name in order:
        card = card_for(cache, name, screened)
        if card is None:
            continue
        today = card["scenarios"]["today"]["score"]
        high = card["scenarios"]["ssp585_2041_2060"]["score"]
        warm = (card.get("shifts") or {}).get("ssp585_2041_2060", {}).get("delta_t_c")
        warm_text = f"+{warm}°C" if warm is not None else "the high screen"
        bits.append(
            f"{CROPS[name]['en']} scores {today} today and {high} under the high 2041–2060 screen "
            f"({warm_text}, score {card['change_high']:+})"
        )
        cards.append(
            {
                "crop": name,
                "crop_en": CROPS[name]["en"],
                "today": today,
                "high_2041_2060": high,
                "change_high": card["change_high"],
            }
        )
    crop_en = CROPS.get(crop, {}).get("en", crop)
    if crop == "ragi":
        lead = (
            f"Before your next sowing window, here is how {screened}'s 2040 heat shift "
            "affects ragi versus drought-hardy tur."
        )
    elif crop == "tur":
        lead = (
            f"Before your next sowing window, here is how {screened}'s 2040 heat shift "
            "affects tur versus drought-hardy ragi."
        )
    else:
        lead = (
            f"Before your next sowing window, here is how {screened}'s 2040 heat shift "
            f"affects {crop_en} versus drought-hardy ragi and tur."
        )
    detail = ". ".join(bits)
    return {
        "text": f"{lead} {detail}." if detail else f"{lead}.",
        "district": screened,
        "cards": cards,
        "citation": (
            "FAO EcoCrop parameters via the dismo ECOcrops table. "
            f"Normals: Open-Meteo 2015–2024 at {screened}. "
            "Deltas: WorldClim CMIP6, mean of MIROC6 and MPI-ESM1-2-HR, 10 arc-minutes, 2041–2060 minus 1970–2000. "
            "This is a screen, not variety advice."
        ),
    }


def card_for(cache: dict, crop: str, district: str) -> dict | None:
    screened = nearest_screened_district(district)
    for card in suitability(cache):
        if card["crop"] == crop and card["district"] == screened:
            card = dict(card)
            card["requested_district"] = district
            card["used_district"] = screened
            return card
    return None
