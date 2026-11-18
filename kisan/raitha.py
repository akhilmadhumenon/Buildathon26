"""Live mandi prices from the RaithaLine board. Caller memory stays in SQLite."""

from __future__ import annotations

import logging
import os
import re
import time

import aiohttp

from kisan.advice import _km_between, _mentioned, format_caller_context
from kisan.db import CROP_ALIASES, DISTRICT_COORDS, KEEPING, Database

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


def live_prices_enabled() -> bool:
    return bool(_base_url())


def _base_url() -> str:
    return os.environ.get("RAITHA_API_URL", DEFAULT_URL).strip().rstrip("/")


async def live_facts(
    session: aiohttp.ClientSession,
    db: Database,
    phone: str,
    caller_point: dict | None,
    turns: list[tuple[str, str]],
) -> str:
    """Price facts for the spoken reply. An empty URL keeps the local demo prices."""
    if not _base_url():
        from kisan.advice import market_facts

        return market_facts(db, phone, caller_point)
    asked = _crops_in_turns(turns)
    try:
        boards = await snapshots(session)
    except Exception:
        logger.exception("raitha board failed")
        boards = []
    if not boards:
        return (
            format_caller_context(db, phone)
            + "\nMarket prices could not be loaded. Ask them to say the question once more. "
            "Do not invent a price."
        )
    return format_market_facts(boards, db, phone, caller_point, asked)


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
    db: Database,
    phone: str,
    caller_point: dict | None,
    focus: list[str] | None = None,
) -> str:
    """A short price sheet. The model speaks only the part the caller asked about."""
    focus = focus or []
    lines = [
        format_caller_context(db, phone),
        "Prices are rupees per quintal from the mandi report. Modal is the usual price. "
        "Min and max are the grade range. A 7-day figure is a prediction. "
        "Shortage yes means this mandi has less supply than usual. "
        "Use the advice when they ask whether to sell here or at another mandi.",
    ]
    detailed = {crop for crop in focus} or {str(board.get("crop") or "") for board in boards}
    for board in boards:
        crop = str(board.get("crop") or "")
        if crop in detailed:
            lines.append(_detail(board))
        else:
            lines.append(_brief(board))
    if KEEPING:
        kept = ". ".join(f"{crop} {days} days {note}" for crop, (days, note) in sorted(KEEPING.items()))
        lines.append("How long the harvest keeps: " + kept + ".")
    lines.extend(_caller_lines(boards, caller_point))
    return "\n".join(line for line in lines if line)


def _detail(board: dict) -> str:
    crop = board.get("crop") or "crop"
    mandi = board.get("mandi") or "the mandi"
    district = board.get("district") or ""
    where = f"{mandi}, {district}" if district else str(mandi)
    parts = [f"{crop} at {where}, reported {board.get('as_of') or 'an unknown day'}."]
    modal = _rupees(board.get("modal"))
    if modal:
        parts.append(f"Modal {modal}.")
    low, high = _rupees(board.get("min_price")), _rupees(board.get("max_price"))
    if low and high:
        parts.append(f"Range {low} to {high}.")
    seasonal = _rupees(board.get("seasonal_modal"))
    if seasonal:
        parts.append(f"Same week last year {seasonal}.")
    parts.append(_supply(board))
    forecast = _rupees(board.get("forecast_7d"))
    parts.append(f"7-day prediction {forecast}." if forecast else "No 7-day prediction.")
    nearby = board.get("nearby") or {}
    if isinstance(nearby, dict) and nearby.get("mandi"):
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
    if advice and advice.lower() != "no advice":
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
