"""Weather, market, and climate claims, and the weekly plan that quotes them."""

from __future__ import annotations

import re
from datetime import date, datetime

from app.climate import card_for, nearest_screened_district
from app.kcc import ground
from app.crops import CALL_WINDOW_DAYS, CROPS, PLACES, canonical_crop, crop_name
from app.forecast import snapshot
from app.weather import rain_outlook

DOSE_WORDS = (
    "dose",
    "pesticide",
    "insecticide",
    "fungicide",
    "herbicide",
    "weedicide",
    "fertilizer",
    "fertiliser",
    "urea",
    "spray",
    "chemical",
    "poison",
)
FARM_WORDS = (
    "price",
    "mandi",
    "sell",
    "hold",
    "crop",
    "sow",
    "harvest",
    "rain",
    "weather",
    "climate",
    "market",
    "arrival",
    "tomato",
    "onion",
    "potato",
    "chilli",
    "chili",
    "banana",
    "ragi",
    "maize",
    "tur",
    "paddy",
    "rice",
    "groundnut",
    "yield",
    "water",
    "grow",
    "sowing",
    "suitab",
)


def classify_question(question: str | None, crop_hint: str | None = None) -> str:
    if not question or not question.strip():
        return "plan"
    text = question.lower()
    if any(word in text for word in DOSE_WORDS):
        return "dose"
    if canonical_crop(text) is None and crop_hint is None and _looks_like_unknown_crop(text):
        return "unknown_crop"
    if any(word in text for word in FARM_WORDS) or canonical_crop(text):
        return "plan"
    return "out_of_domain"


def _looks_like_unknown_crop(text: str) -> bool:
    match = re.search(r"\b(grow|crop|plant)\b", text)
    return bool(match) and canonical_crop(text) is None and not any(name in text for name in CROPS)


def refusal(kind: str, detail: str) -> dict:
    messages = {
        "dose": "RaithaLine does not give pesticide or fertilizer doses. Contact the nearest Krishi Vigyan Kendra.",
        "out_of_domain": "That is outside this line. It can talk about the mandi price, the rain, and the crop on this farm.",
        "unknown_crop": "That crop is not on this line. Choose one of: " + ", ".join(CROPS[c]["en"] for c in CROPS) + ".",
        "missing_series": "There is no mandi series for that crop, so this line will not guess a price.",
    }
    return {"refused": True, "kind": kind, "message": messages[kind], "detail": detail}


def market_claims(snap: dict) -> list[dict]:
    if not snap.get("available"):
        return [
            {
                "agent": "market",
                "claim": snap["action_reason"],
                "citation": snap["citation"],
            }
        ]
    claim = (
        f"{crop_name(snap['crop'], 'en')} at {snap['mandi']}. {snap.get('grade_line') or ''} "
        f"Reported day: {snap['as_of']}."
    )
    if snap.get("arrival_7d_tonnes") is not None:
        claim += f" Arrivals over the last 7 days are {snap['arrival_7d_tonnes']:.1f} tonnes."
    if snap.get("seasonal_modal") is not None:
        claim += f" The same week last year was Rs {snap['seasonal_modal']:.0f}."
    if snap.get("forecast_7d") is not None:
        claim += f" The 7-day model forecast is Rs {snap['forecast_7d']:.0f}."
    if snap.get("forecast_30d_seasonal") is not None:
        claim += (
            f" The 30-day figure is the same season last year shifted, Rs {snap['forecast_30d_seasonal']:.0f}, "
            "not a second model."
        )
    claim += f" Advice: {snap.get('action_label') or snap['action']}. {snap['action_reason']}"
    if snap.get("shortage") is True:
        claim += " The mandi looks short: 7-day arrivals are under 60 percent of the same week last year and the modal is in the top fifth of the last 90 days."
    elif snap.get("shortage") is False:
        claim += " The shortage rule is not met."
    claims = [{"agent": "market", "claim": claim, "citation": snap["citation"]}]
    nearby = snap.get("nearby")
    if nearby:
        claims.append(
            {
                "agent": "market",
                "claim": (
                    f"Nearby {nearby['mandi']} modal is Rs {nearby['modal']:.0f} on {nearby['as_of']}. "
                    f"Transport is Rs {nearby['transport_rs_per_qtl_per_km']} per quintal per km "
                    f"over {nearby['distance_km']} km, Rs {nearby['transport_rs_per_qtl']} per quintal. "
                    f"Worth the trip: {nearby['worth_the_trip']}. "
                    "A nearby sale is an FPO pooled trip, not a solo diversion from a local commission agent."
                ),
                "citation": snap["citation"],
            }
        )
    return claims


def weather_claims(cache: dict, district: str) -> list[dict]:
    outlook = rain_outlook(cache, district if district in (cache.get("places") or {}) else district)
    if not outlook["known"] and district not in (cache.get("places") or {}):
        # Fall back to the nearest cached district we have coordinates for.
        if district in PLACES:
            outlook = rain_outlook(cache, district)
    if not outlook["known"]:
        return [
            {
                "agent": "weather",
                "claim": f"No rain forecast is cached for {district}.",
                "citation": outlook["citation"],
            }
        ]
    if outlook["heavy"]:
        claim = (
            f"Heavy rain is on the forecast for {outlook['district']}: "
            f"{outlook['next_2d_mm']} mm over the next two days. Do not ask the farmer to transport."
        )
    else:
        claim = (
            f"Rain over the next two days at {outlook['district']} is {outlook['next_2d_mm']} mm, "
            "under the 20 mm transport hold."
        )
    return [{"agent": "weather", "claim": claim, "citation": outlook["citation"]}]


def climate_claims(cache: dict, crop: str, district: str, soil_type: str | None) -> list[dict]:
    card = card_for(cache, crop, district)
    if card is None:
        return [
            {
                "agent": "climate",
                "claim": "No climate score is available.",
                "citation": "FAO EcoCrop parameter table in data/ecocrop.json.",
            }
        ]
    today = card["scenarios"]["today"]["score"]
    moderate = card["scenarios"]["ssp245_2041_2060"]["score"]
    high = card["scenarios"]["ssp585_2041_2060"]["score"]
    where = card["used_district"]
    extra = ""
    if card["requested_district"] != where:
        extra = f" {card['requested_district']} uses the nearer screened district, {where}."
    soil = (
        f" Soil is recorded as farmer-reported {soil_type}. It was not checked against a soil grid."
        if soil_type
        else " Soil was not provided and was not looked up."
    )
    return [
        {
            "agent": "climate",
            "claim": (
                f"{card['eco_name']} ({card['scientific_name']}) scores {today} today at {where}, "
                f"{moderate} under the moderate 2041–2060 screen "
                f"(+{card['shifts']['ssp245_2041_2060']['delta_t_c']}°C, rain ×"
                f"{card['shifts']['ssp245_2041_2060']['precip_ratio']}), and {high} under the high screen "
                f"(+{card['shifts']['ssp585_2041_2060']['delta_t_c']}°C, rain ×"
                f"{card['shifts']['ssp585_2041_2060']['precip_ratio']}). "
                f"The score is 60 percent temperature and 40 percent rainfall against FAO EcoCrop limits."
                f"{extra}{soil} This is a screen, not variety advice."
            ),
            "citation": (
                "FAO EcoCrop parameters via the dismo ECOcrops table "
                f"({card['scientific_name']}). Normals: Open-Meteo 2015–2024 at {where}. "
                "Deltas: WorldClim CMIP6, mean of MIROC6 and MPI-ESM1-2-HR at 10 arc-minutes, "
                "2041–2060 minus the 1970–2000 baseline."
            ),
        }
    ]


def weekly_plan(frame, tomato_model, weather_cache, climate_cache, body: dict, gates: dict | None = None, connection=None) -> dict:
    question = body.get("question")
    crop = canonical_crop(body.get("crop"))
    kind = classify_question(question, crop)
    if kind == "dose":
        hit = ground(question, crop, connection)
        if hit:
            return {
                "refused": False,
                "grounded": True,
                "layer": hit["layer"],
                "crop": crop,
                "district": body.get("district"),
                "plan": hit["text"],
                "claims": [{"agent": "kcc", "claim": hit["text"], "citation": hit["citation"]}],
                "kcc_id": hit["id"],
            }
        return refusal("dose", question or "")
    if kind == "out_of_domain":
        return refusal("out_of_domain", question or "")
    if crop is None:
        return refusal("unknown_crop", body.get("crop") or question or "")
    district = (body.get("district") or CROPS[crop]["district"]).strip()
    snap = snapshot(frame, crop, tomato_model, (gates or {}).get(crop))
    if not snap["available"]:
        return refusal("missing_series", crop)
    soil = body.get("soil_type")
    weather_district = district if district in PLACES else snap["district"]
    claims = (
        market_claims(snap)
        + weather_claims(weather_cache, weather_district)
        + climate_claims(climate_cache, crop, district, soil)
    )
    if question and kind == "plan":
        hit = ground(question, crop, connection, minimum=0.5)
        if hit:
            claims.append({"agent": "kcc", "claim": hit["text"], "citation": hit["citation"]})
    sowing = body.get("sowing_date")
    window = ""
    if sowing:
        try:
            sown = datetime.strptime(sowing, "%Y-%m-%d").date()
            age = (date.today() - sown).days
            start, end = CALL_WINDOW_DAYS[crop]
            open_now = start <= age <= end
            window = (
                f" Sown {sowing}, {age} days ago. The call window for {crop_name(crop, 'en')} "
                f"is day {start} to {end}. Harvest window open: {open_now}."
            )
        except ValueError:
            window = " The sowing date was not a YYYY-MM-DD date, so the harvest window was not scored."
    screened = nearest_screened_district(district if district in PLACES else snap["district"])
    lines = [
        f"Weekly plan for {crop_name(crop, 'en')}. Farmer district: {district}. Climate screen: {screened}.",
        window.strip(),
    ]
    for claim in claims:
        lines.append(f"{claim['agent'].title()}: {claim['claim']}")
    lines.append("The farmer decides. This plan does not dispatch produce or recommend a chemical dose.")
    if question and kind == "plan":
        lines.append(f"Question noted: {question.strip()} The answer above uses only the agent claims.")
    return {
        "refused": False,
        "crop": crop,
        "district": district,
        "plan": " ".join(part for part in lines if part),
        "claims": claims,
        "snapshot": {key: value for key, value in snap.items() if key != "points"},
    }
