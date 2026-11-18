"""Open-Meteo rain for the districts we can call, cached on disk."""

from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from app.crops import PLACES

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache" / "weather.json"
CTX = ssl._create_unverified_context()
HEAVY_RAIN_MM = 20.0
SOURCE = "Open-Meteo forecast API, daily precipitation_sum, timezone Asia/Kolkata."


def _fetch_place(name: str, lat: float, lon: float) -> dict:
    query = urllib.parse.urlencode(
        {
            "latitude": lat,
            "longitude": lon,
            "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min",
            "forecast_days": 7,
            "timezone": "Asia/Kolkata",
        }
    )
    url = f"https://api.open-meteo.com/v1/forecast?{query}"
    with urllib.request.urlopen(url, timeout=40, context=CTX) as response:
        payload = json.loads(response.read().decode())
    daily = payload["daily"]
    return {
        "district": name,
        "lat": lat,
        "lon": lon,
        "dates": daily["time"],
        "precipitation_mm": daily["precipitation_sum"],
        "tmax": daily["temperature_2m_max"],
        "tmin": daily["temperature_2m_min"],
    }


def refresh() -> dict:
    places = {name: _fetch_place(name, lat, lon) for name, (lat, lon) in PLACES.items()}
    body = {
        "source": SOURCE,
        "heavy_rain_mm": HEAVY_RAIN_MM,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "places": places,
    }
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(body))
    return body


def _fresh(body: dict) -> bool:
    places = body.get("places") or {}
    dates = []
    for place in places.values():
        if place.get("dates"):
            dates.append(place["dates"][0])
    if not dates:
        return False
    return min(dates) >= date.today().isoformat()


def load() -> dict:
    cached = json.loads(CACHE.read_text()) if CACHE.exists() else None
    if cached and _fresh(cached):
        return cached
    try:
        return refresh()
    except (OSError, json.JSONDecodeError, TimeoutError, ValueError):
        if cached:
            return cached
        raise


def rain_outlook(cache: dict, district: str) -> dict:
    place = (cache.get("places") or {}).get(district)
    if not place:
        return {
            "known": False,
            "district": district,
            "next_2d_mm": None,
            "heavy": False,
            "citation": f"No cached Open-Meteo forecast for {district}.",
        }
    amounts = place["precipitation_mm"][:2]
    total = float(sum(value or 0 for value in amounts))
    return {
        "known": True,
        "district": district,
        "dates": place["dates"][:2],
        "next_2d_mm": round(total, 1),
        "heavy": total >= HEAVY_RAIN_MM,
        "citation": (
            f"{SOURCE} {district} ({place['lat']}, {place['lon']}), "
            f"next two days {place['dates'][0]} to {place['dates'][1]}."
        ),
    }
