"""Live mandi prices from the RaithaLine dashboard. There is no local price store."""

from __future__ import annotations

import logging
import os
import re
import time

import aiohttp

from kisan.advice import _km_between, _mentioned
from kisan.reference import CROP_ALIASES, DISTRICT_COORDS, KEEPING

logger = logging.getLogger("kisan.exotel")

DEFAULT_URL = "https://raithaline-production.up.railway.app"
DEFAULT_CROPS = (
    "tomato",
    "onion",
    "potato",
    "chilli",
    "banana",
    "ragi",
    "maize",
    "tur",
    "paddy",
    "groundnut",
)
_CACHE: list[dict] = []
_CACHE_AT = 0.0
_TTL_SECONDS = 600


def board_url() -> str:
    return _base_url()


def _base_url() -> str:
    raw = os.environ.get("RAITHA_API_URL")
    if raw is None:
        return DEFAULT_URL
    return raw.strip().rstrip("/") or DEFAULT_URL


async def live_facts(
    session: aiohttp.ClientSession,
    phone: str,
    caller_point: dict | None,
    turns: list[tuple[str, str]],
) -> str:
    """Price facts for the spoken reply. The dashboard is the only price source."""
    asked = _crops_in_turns(turns)
    latest = next((text for role, text in reversed(turns) if role == "user"), "")
    try:
        boards = await snapshots(session)
    except Exception:
        logger.exception("raitha board failed")
        boards = []
    need = _question_need(latest)
    parts: list[str] = []
    if boards:
        parts.append(format_market_facts(boards, phone, caller_point, asked, need))
    if "plant" in need:
        parts.append(await _planting_facts(session, caller_point, latest, asked))
    if parts:
        return "\n".join(part for part in parts if part)
    return (
        f"Caller phone: {phone or 'unknown'}.\n"
        "Market prices could not be loaded. Ask them to say the question once more. "
        "Do not invent a price."
    )


async def snapshots(session: aiohttp.ClientSession) -> list[dict]:
    global _CACHE_AT
    now = time.monotonic()
    if _CACHE and now - _CACHE_AT < _TTL_SECONDS:
        return list(_CACHE)
    crops = await _crop_ids(session)
    timeout = aiohttp.ClientTimeout(total=8)
    loaded = await _gather(session, crops, timeout)
    if loaded:
        _CACHE[:] = loaded
        _CACHE_AT = now
    return list(_CACHE or loaded)


async def _crop_ids(session: aiohttp.ClientSession) -> tuple[str, ...]:
    try:
        async with session.get(f"{_base_url()}/v1/health", timeout=aiohttp.ClientTimeout(total=4)) as response:
            if response.status != 200:
                return DEFAULT_CROPS
            body = await response.json(content_type=None)
    except Exception:
        logger.warning("raitha health failed", exc_info=True)
        return DEFAULT_CROPS
    crops = body.get("crops_loaded") if isinstance(body, dict) else None
    if not crops:
        return DEFAULT_CROPS
    return tuple(str(crop) for crop in crops)


async def _gather(session: aiohttp.ClientSession, crops: tuple[str, ...], timeout: aiohttp.ClientTimeout) -> list[dict]:
    import asyncio

    found = await asyncio.gather(*(_one(session, crop, timeout) for crop in crops), return_exceptions=True)
    boards = []
    for item in found:
        if isinstance(item, dict) and item.get("available"):
            boards.append(item)
        elif isinstance(item, Exception):
            logger.warning("raitha crop failed: %s", item)
    return boards


async def _one(session: aiohttp.ClientSession, crop: str, timeout: aiohttp.ClientTimeout) -> dict:
    async with session.get(
        f"{_base_url()}/v1/admin/board",
        params={"crop": crop},
        timeout=timeout,
    ) as response:
        response.raise_for_status()
        body = await response.json(content_type=None)
    snap = dict((body or {}).get("snapshot") or {})
    snap.pop("points", None)
    snap["crop"] = str(snap.get("crop") or crop)
    return snap


def format_market_facts(
    boards: list[dict],
    phone: str,
    caller_point: dict | None,
    focus: list[str] | None = None,
    need: set[str] | None = None,
) -> str:
    """A short price sheet. Extra lines are included only for what this question asked."""
    focus = focus or []
    lines = [f"Caller phone: {phone or 'unknown'}.", _fact_rule(need)]
    # An open planting question has no crop yet, so keep every price line short.
    plant_survey = bool(need and "plant" in need and not focus)
    detailed = set() if plant_survey else (
        {crop for crop in focus} or {str(board.get("crop") or "") for board in boards}
    )
    for board in boards:
        crop = str(board.get("crop") or "")
        if crop in detailed:
            lines.append(_detail(board, need))
        else:
            lines.append(_brief(board))
    if need and "wait" in need and KEEPING:
        kept = [
            f"{crop} {days} days {note}"
            for crop, (days, note) in sorted(KEEPING.items())
            if not focus or crop in focus
        ]
        if kept:
            lines.append("How long the harvest keeps: " + ". ".join(kept) + ".")
    lines.extend(_caller_lines(boards, caller_point))
    return "\n".join(line for line in lines if line)


def _question_need(text: str) -> set[str]:
    """Which fact lines this sentence actually needs. A bare price stays a price."""
    needed: set[str] = set()
    if _is_sell(text) or _asks_other(text):
        needed.add("sell")
    if _asks_wait(text):
        needed.add("wait")
    if _asks_forecast(text):
        needed.add("forecast")
    if _asks_grade(text):
        needed.add("grade")
    if _asks_plant(text):
        needed.add("plant")
    if _asks_price(text):
        needed.add("price")
    if not needed:
        needed.add("price")
    return needed


def _fact_rule(need: set[str] | None) -> str:
    if need and "plant" in need and "price" not in need and "sell" not in need:
        return (
            "They asked what to plant. Recommend from the weekly plan scores. "
            "Name the crop with the higher today score. Do not invent a crop or a score."
        )
    if need == {"price"}:
        return (
            "They asked for a price. Say the modal, the mandi, and the reported date. "
            "Do not mention supply, a forecast, another mandi, or whether to sell."
        )
    return (
        "Prices are rupees per quintal from the mandi report. Modal is the usual price. "
        "Use a forecast, the grade range, another mandi, or the sell advice only when that line is included below."
    )


_PLANT = (
    "plant",
    "planting",
    "sow",
    "sowing",
    "which crop",
    "what crop",
    "what should i grow",
    "बोना",
    "बोऊं",
    "बोऊँ",
    "बोऊ",
    "कौन सी फसल",
    "कौनसी फसल",
    "कौन-सी फसल",
    "फसल लगा",
    "फसल बो",
    "लगाना",
    "लगाने",
    "लगाऊं",
    "लगाऊँ",
    "ಬಿತ್ತ",
    "ನಾಟಿ",
    "ಯಾವ ಬೆಳೆ",
    "ಬೆಳೆ ಯಾವುದು",
    "ಬೆಳೆಯಲು",
    "ಬೆಳೆಯಬೇಕು",
)
_PRICE = ("price", "rate", "भाव", "दाम", "रेट", "ಬೆಲೆ", "ದರ", "ದಾಮ")
_WHICH_CROP = ("which crop", "what crop", "कौन सी फसल", "कौनसी फसल", "ಕೌನ್", "ಯಾವ ಬೆಳೆ", "ಬೆಳೆ ಯಾವುದು")
_SCORE = re.compile(
    r"scores\s+([0-9]+(?:\.[0-9]+)?)\s+today\b.*?([0-9]+(?:\.[0-9]+)?)\s+under the moderate\b"
    r".*?([0-9]+(?:\.[0-9]+)?)\s+under the high\b",
    re.IGNORECASE | re.DOTALL,
)
_PLAN_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}


def _asks_plant(question: str) -> bool:
    text = (question or "").lower()
    return any(word in text for word in _PLANT)


def _asks_price(question: str) -> bool:
    text = (question or "").lower()
    return any(word in text for word in _PRICE)


def _asks_which_crop(question: str) -> bool:
    text = (question or "").lower()
    return any(word in text for word in _WHICH_CROP)


def _planting_crops(latest: str, asked: list[str]) -> tuple[list[str], bool]:
    """Crops to score, and whether the farmer already named the crop."""
    named = _mentioned(latest, CROP_ALIASES)
    if named:
        return named, True
    if _asks_which_crop(latest) or not asked:
        return list(DEFAULT_CROPS), False
    return list(asked), True


def _district_name(caller_point: dict | None) -> str:
    """District the weekly plan should score. Empty when the call has no place."""
    if not caller_point:
        return ""
    name = str(caller_point.get("name") or "").strip().lower()
    for district in DISTRICT_COORDS:
        if district in name:
            return district.title()
    lat, lon = caller_point.get("lat"), caller_point.get("lon")
    if lat is None or lon is None or not DISTRICT_COORDS:
        return ""
    nearest = min(
        DISTRICT_COORDS,
        key=lambda district: _km_between(lat, lon, DISTRICT_COORDS[district][0], DISTRICT_COORDS[district][1]),
    )
    return nearest.title()


def format_planting(plans: list[dict], district: str, named: bool) -> str:
    """One recommendation sheet from weekly-plan climate claims."""
    if not district:
        return (
            "Weekly planting plan: their district is not known. "
            "Ask where they farm before naming a crop. Do not invent a crop."
        )
    ranked: list[tuple[float, str]] = []
    weather = ""
    for body in plans:
        line, today, claim_weather = _plan_score(body)
        if line is None or today is None:
            continue
        ranked.append((today, line))
        weather = weather or claim_weather
    if not ranked:
        return (
            f"Weekly planting plans for {district} could not be loaded. "
            "Ask them to say the question once more. Do not invent a crop or a score."
        )
    ranked.sort(key=lambda item: item[0], reverse=True)
    if named and len(ranked) == 1:
        lead = (
            f"Weekly plan for the crop they named, at {district}. "
            "Say whether this crop suits this district. A higher today score suits it better. "
            "Do not switch them to a different crop."
        )
    else:
        lead = (
            f"Weekly planting plans for {district}. "
            "Recommend one crop, the highest today score. "
            "If several share that score, name one and say the others match it. Do not read every score."
        )
    lines = [lead, *[line for _, line in ranked]]
    if weather:
        lines.append(f"Weather: {weather}")
    return "\n".join(lines)


def _plan_score(body: dict) -> tuple[str | None, float | None, str]:
    if not isinstance(body, dict) or body.get("refused"):
        return None, None, ""
    claims = body.get("claims") if isinstance(body.get("claims"), list) else []
    climate = ""
    weather = ""
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        text = str(claim.get("claim") or "").strip()
        if claim.get("agent") == "climate":
            climate = text
        elif claim.get("agent") == "weather" and text:
            weather = text
    match = _SCORE.search(climate)
    if not match:
        return None, None, weather
    today = float(match.group(1))
    crop = body.get("crop") or "crop"
    where = body.get("district") or ""
    line = (
        f"- {crop} at {where}: today {match.group(1)}, "
        f"moderate {match.group(2)}, high {match.group(3)}."
    )
    return line, today, weather


async def _planting_facts(
    session: aiohttp.ClientSession,
    caller_point: dict | None,
    latest: str,
    asked: list[str],
) -> str:
    district = _district_name(caller_point)
    crops, named = _planting_crops(latest, asked)
    if not district:
        return format_planting([], "", named)
    timeout = aiohttp.ClientTimeout(total=8)
    import asyncio

    found = await asyncio.gather(
        *(_one_plan(session, crop, district, latest, timeout) for crop in crops),
        return_exceptions=True,
    )
    plans = []
    for item in found:
        if isinstance(item, dict):
            plans.append(item)
        elif isinstance(item, Exception):
            logger.warning("raitha weekly plan failed: %s", item)
    return format_planting(plans, district, named and len(crops) == 1)


async def _one_plan(
    session: aiohttp.ClientSession,
    crop: str,
    district: str,
    question: str,
    timeout: aiohttp.ClientTimeout,
) -> dict:
    key = (crop, district.lower())
    cached = _PLAN_CACHE.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < _TTL_SECONDS:
        return cached[1]
    payload = {"crop": crop, "district": district, "question": (question or "")[:400]}
    async with session.post(
        f"{_base_url()}/v1/plans/weekly",
        json=payload,
        timeout=timeout,
    ) as response:
        response.raise_for_status()
        body = await response.json(content_type=None)
    plan = body if isinstance(body, dict) else {}
    if plan:
        _PLAN_CACHE[key] = (now, plan)
    return plan


_WAIT = ("wait", "spoil", "perish", "store", "how long", "कितने दिन", "ಎಷ್ಟು ದಿನ", "ಕೆಡ", "खराब")
_FORECAST = ("forecast", "prediction", "next week", "7 day", "7-day", "अगले", "ಮುಂದಿನ")
_GRADE = ("grade", "quality", "range", "गुणवत्ता", "ಗುಣಮಟ್ಟ")


def _asks_wait(question: str) -> bool:
    text = (question or "").lower()
    return any(word in text for word in _WAIT)


def _asks_forecast(question: str) -> bool:
    text = (question or "").lower()
    return any(word in text for word in _FORECAST)


def _asks_grade(question: str) -> bool:
    text = (question or "").lower()
    return any(word in text for word in _GRADE)


def _detail(board: dict, need: set[str] | None = None) -> str:
    crop = board.get("crop") or "crop"
    mandi = board.get("mandi") or "the mandi"
    district = board.get("district") or ""
    where = f"{mandi}, {district}" if district else str(mandi)
    parts = [f"{crop} at {where}, reported {board.get('as_of') or 'an unknown day'}."]
    modal = _rupees(board.get("modal"))
    if modal:
        parts.append(f"Modal {modal}.")
    wanted = need if need is not None else {"sell", "forecast", "grade", "wait"}
    if "grade" in wanted:
        low, high = _rupees(board.get("min_price")), _rupees(board.get("max_price"))
        if low and high:
            parts.append(f"Range {low} to {high}.")
    if "sell" in wanted or need is None:
        seasonal = _rupees(board.get("seasonal_modal"))
        if seasonal and need is None:
            parts.append(f"Same week last year {seasonal}.")
        parts.append(_supply(board))
    if "forecast" in wanted or "wait" in wanted:
        forecast = _rupees(board.get("forecast_7d"))
        parts.append(f"7-day prediction {forecast}." if forecast else "No 7-day prediction.")
    nearby = board.get("nearby") or {}
    if ("sell" in wanted or need is None) and isinstance(nearby, dict) and nearby.get("mandi"):
        near_modal = _rupees(nearby.get("modal"))
        distance = nearby.get("distance_km")
        transport = _rupees(nearby.get("transport_rs_per_qtl"))
        trip = (
            f"Nearby {nearby['mandi']}"
            + (f" modal {near_modal}" if near_modal else "")
            + (f", {int(round(float(distance)))} km" if distance is not None else "")
            + (f", transport {transport} per quintal" if transport else "")
            + "."
        )
        if nearby.get("worth_the_trip") is True:
            trip += " The trip is worth it."
        elif nearby.get("worth_the_trip") is False:
            trip += " The trip is not worth it."
        parts.append(trip)
    advice = str(board.get("action_label") or "").strip()
    reason = " ".join(str(board.get("action_reason") or "").split())
    if ("sell" in wanted or need is None) and advice and advice.lower() != "no advice":
        if len(reason) > 280:
            reason = reason[:277].rstrip() + "..."
        parts.append(f"Advice if they ask whether to sell: {advice}. {reason}".rstrip())
    return " ".join(parts)


def _supply(board: dict) -> str:
    arrival = _tonnes(board.get("arrival_7d_tonnes"))
    usual = _tonnes(board.get("seasonal_arrival_7d_tonnes"))
    shortage = board.get("shortage")
    if shortage is True:
        flag = "shortage yes"
    elif shortage is False:
        flag = "shortage no"
    else:
        flag = "shortage unknown"
    parts = []
    if arrival:
        parts.append(f"arrivals {arrival} tonnes this week")
    if usual:
        parts.append(f"usual {usual} tonnes")
    parts.append(flag)
    return "Supply: " + ", ".join(parts) + "."


def _tonnes(value: object) -> str:
    if value is None or value == "":
        return ""
    try:
        return str(int(round(float(value))))
    except (TypeError, ValueError):
        return ""


def _brief(board: dict) -> str:
    modal = _rupees(board.get("modal"))
    mandi = board.get("mandi") or "mandi"
    return f"{board.get('crop')} at {mandi}: modal {modal}, reported {board.get('as_of') or 'unknown'}."


def _caller_lines(boards: list[dict], caller_point: dict | None) -> list[str]:
    if caller_point and caller_point.get("lat") is None:
        return [
            f"Caller said {caller_point['name']}. The map did not find it. Ask them to say the place once more."
        ]
    if not caller_point:
        return ["Caller place: unknown."]
    lines = [
        f"Caller map point: {caller_point['name']} at {caller_point['lat']:.4f}, {caller_point['lon']:.4f}."
    ]
    ranked = []
    seen: set[str] = set()
    for board in boards:
        district = str(board.get("district") or "")
        coords = DISTRICT_COORDS.get(district.lower())
        if not coords or district.lower() in seen:
            continue
        seen.add(district.lower())
        kilometres = _km_between(caller_point["lat"], caller_point["lon"], coords[0], coords[1])
        ranked.append((kilometres, district, board.get("mandi") or district))
    ranked.sort()
    if not ranked:
        return lines
    lines.append(f"Distance from {caller_point['name']} to reporting districts:")
    for kilometres, district, mandi in ranked:
        lines.append(f"- {caller_point['name']} to {district} ({mandi}): {int(round(kilometres))} km.")
    return lines


def _crops_in_turns(turns: list[tuple[str, str]]) -> list[str]:
    found: list[str] = []
    for role, text in turns:
        if role != "user":
            continue
        for crop in _mentioned(text, CROP_ALIASES):
            if crop not in found:
                found.append(crop)
    return found


def _rupees(value: object) -> str:
    if value is None or value == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    return f"{int(round(number))}"


_CROP_WORD = {
    "kn": {
        "maize": "ಜೋಳ",
        "tomato": "ಟೊಮೆಟೊ",
        "potato": "ಆಲೂಗಡ್ಡೆ",
        "onion": "ಈರುಳ್ಳಿ",
        "paddy": "ಭತ್ತ",
        "ragi": "ರಾಗಿ",
        "chilli": "ಮೆಣಸಿನಕಾಯಿ",
        "banana": "ಬಾಳೆಹಣ್ಣು",
    },
    "hi": {
        "maize": "मक्का",
        "tomato": "टमाटर",
        "potato": "आलू",
        "onion": "प्याज",
        "paddy": "धान",
        "ragi": "रागी",
        "chilli": "मिर्च",
        "banana": "केला",
    },
}
_OTHER_MANDI = ("ಅಥವಾ", "ಬೇರೆ", "ಹತ್ತಿರ", "अथवा", "दूसरी", "nearby", "another", " or ")


def speak_board(question: str, language: str) -> str:
    """A short price line from the cached board when the model cannot answer."""
    crops = _mentioned(question, CROP_ALIASES)
    if not crops or not _CACHE:
        return ""
    board = next((item for item in _CACHE if item.get("crop") == crops[-1]), None)
    if not board:
        return ""
    language = language if language in {"en", "hi", "kn"} else "en"
    nearby = board.get("nearby") if isinstance(board.get("nearby"), dict) else {}
    if nearby and nearby.get("worth_the_trip") is True and (_is_sell(question) or _asks_other(question)):
        return _nearby_line(language, board, nearby)
    return _here_line(language, board)


def _is_sell(question: str) -> bool:
    text = (question or "").lower()
    for word in ("sell", "bechna", "bechne", "becho", "bech", "ಮಾರ", "बेच"):
        if any(ord(char) > 127 for char in word):
            if word in text:
                return True
        elif re.search(rf"(?<!\w){word}(?!\w)", text):
            return True
    return False


def _asks_other(question: str) -> bool:
    return any(word in question.lower() for word in _OTHER_MANDI)


def _here_line(language: str, board: dict) -> str:
    crop = _crop_word(language, str(board.get("crop") or ""))
    mandi = str(board.get("district") or board.get("mandi") or "")
    modal = _rupees(board.get("modal"))
    short = board.get("shortage") is True
    if language == "kn":
        line = f"{mandi} ಮಂಡಿಯಲ್ಲಿ {crop} {modal} ರೂಪಾಯಿ ಕ್ವಿಂಟಲ್."
        if short:
            line += " ಸರಕು ಕಡಿಮೆ ಇದೆ. ಇಲ್ಲಿ ಮಾರಿ."
        return line
    if language == "hi":
        line = f"{_spoken_place('hi', mandi)} मंडी में {crop} {modal} रुपये क्विंटल है।"
        if short:
            line += " आपूर्ति कम है। यहीं बेचिए!"
        return line
    line = f"{crop} at {mandi} is {modal} rupees a quintal."
    if short:
        line += " Supply is short. Sell there!"
    return line


def _nearby_line(language: str, board: dict, nearby: dict) -> str:
    crop = _crop_word(language, str(board.get("crop") or ""))
    home = _spoken_place(language, str(board.get("district") or board.get("mandi") or ""))
    other = _spoken_place(language, str(nearby.get("district") or nearby.get("mandi") or ""))
    modal = _rupees(board.get("modal"))
    near = _rupees(nearby.get("modal"))
    kilometres = nearby.get("distance_km")
    km = str(int(round(float(kilometres)))) if kilometres is not None else ""
    if language == "kn":
        return f"{home}ನಲ್ಲಿ {crop} {modal} ರೂಪಾಯಿ. {other}ನಲ್ಲಿ {near} ರೂಪಾಯಿ, {km} ಕಿಲೋಮೀಟರ್. ಅಲ್ಲಿಗೆ ಹೋಗಿ!"
    if language == "hi":
        return f"{home} में {crop} {modal} रुपये है। {other} में {near} रुपये मिलता है, करीब {km} किलोमीटर दूर। वहीं बेचिए!"
    return f"{crop} at {home} is {modal} rupees. {other} pays {near}, about {km} kilometres away. Go there!"


def _crop_word(language: str, crop: str) -> str:
    return _CROP_WORD.get(language, {}).get(crop, crop)


_PLACE_WORD = {
    "hi": {
        "kolar": "कोलार",
        "bengaluru": "बेंगलुरु",
        "bangalore": "बेंगलुरु",
        "davangere": "दावणगेरे",
        "hassan": "हसन",
        "hubballi": "हुबली",
        "dharwad": "धारवाड़",
        "raichur": "रायचूर",
        "mysuru": "मैसूर",
        "chitradurga": "चित्रदुर्ग",
        "kalaburagi": "कलबुर्गी",
    },
    "kn": {
        "kolar": "ಕೋಲಾರ",
        "bengaluru": "ಬೆಂಗಳೂರು",
        "bangalore": "ಬೆಂಗಳೂರು",
        "davangere": "ದಾವಣಗೆರೆ",
        "hassan": "ಹಾಸನ",
        "raichur": "ರಾಯಚೂರು",
        "mysuru": "ಮೈಸೂರು",
    },
}


def _spoken_place(language: str, raw: str) -> str:
    text = (raw or "").lower()
    for key, word in _PLACE_WORD.get(language, {}).items():
        if key in text:
            return word
    return raw
