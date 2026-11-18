"""Find a village or town on the map, then measure it against the mandis."""

from __future__ import annotations

import re

import aiohttp

from kisan.reference import CROP_ALIASES, DISTRICT_COORDS, PLACE_ALIASES, Caller, canonical_place
from kisan.names import skeleton

GEOCODE_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "KisanSahayak/1.0 (local farmer calling demo)"

_STOP = {
    "a", "an", "the", "to", "from", "in", "at", "on", "of", "for", "me", "my", "i", "am",
    "is", "are", "was", "what", "which", "where", "who", "how", "near", "nearest", "closest",
    "nearby", "around", "close", "mandi", "market", "please", "tell", "calling", "call",
    "here", "there", "village", "town", "place", "district", "and", "or", "can", "sell",
    "buy", "price", "today", "now", "best", "deal", "you", "your", "we", "our", "this",
    "that", "with", "about", "should", "would", "could", "want", "need", "give", "get",
    "sorry", "hello", "hi", "hey", "namaste", "namaskar", "thanks", "thank", "bye",
    "goodbye", "nothing", "no", "yes", "okay", "ok", "sir", "madam", "live", "living",
    "stay", "staying", "if", "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday", "mother", "gonna", "make",
}

_cache: dict[str, dict | None] = {}

_LOCATING = {
    "from", "calling", "call", "live", "living", "stay", "staying",
    "village", "town", "near", "nearest", "closest", "nearby",
}
_NOT_A_PLACE = ("sell", "better", "another", "other", "worth", "बेच", "बेहतर", "दूसरी", "ಮಾರ", "ಬೇರೆ")


def named_place(text: str) -> str:
    """A place the farmer just gave, when the sentence is about where they are."""
    raw = text or ""
    words = re.findall(r"[^\W\d_]+", raw.lower())
    content = [word for word in words if word not in _STOP]
    unknown: list[str] = []
    known: list[str] = []
    has_crop = False
    for word in content:
        if word in CROP_ALIASES:
            has_crop = True
            continue
        if word in PLACE_ALIASES:
            known.append(PLACE_ALIASES[word])
            continue
        if len(word) < 4:
            continue
        unknown.append(word)
    locating = any(word in words for word in _LOCATING)
    bare_name = (
        not has_crop
        and not locating
        and len(content) <= 3
        and not any(marker in raw.lower() for marker in _NOT_A_PLACE)
    )
    if not locating and not bare_name:
        return ""
    if unknown and (not has_crop or locating):
        return " ".join(unknown[:3])
    if known and not has_crop:
        return known[-1]
    return ""


async def locate(session: aiohttp.ClientSession, query: str) -> dict | None:
    """A known mandi coordinate, or a map search for a village such as Kudlu."""
    key = " ".join((query or "").strip().lower().split())
    if not key or len(key) < 4:
        return None
    if key in _cache:
        return _cache[key]
    known = _known_point(key)
    if known:
        _cache[key] = known
        return known
    point = await _first_match(session, key)
    _cache[key] = point
    return point


async def caller_point(
    session: aiohttp.ClientSession,
    caller: Caller,
    turns: list[tuple[str, str]],
) -> dict | None:
    """Where the farmer is, from this sentence or from the place named earlier on this call."""
    latest = next((text for role, text in reversed(turns) if role == "user"), "")
    spoken = named_place(latest)
    if spoken:
        point = await locate(session, spoken)
        if point:
            caller.place = point["name"]
            return {**point, "from_utterance": True}
        return {"name": spoken, "lat": None, "lon": None, "from_utterance": True}
    stored = caller.place
    if not stored or len(stored) < 4:
        return None
    point = await locate(session, stored)
    if not point:
        return None
    return {**point, "from_utterance": False}


def _place_queries(key: str) -> list[str]:
    """Spoken 'Singas Andhra' is the place Singasandra once the gap and the h are removed."""
    compact = re.sub(r"\s+", "", key)
    repaired = compact.replace("h", "")
    queries: list[str] = []
    for item in (repaired, key, compact, _fold_place(repaired), _fold_place(key)):
        item = " ".join(item.split())
        if len(item) >= 4 and item not in queries:
            queries.append(item)
    return queries


async def _first_match(session: aiohttp.ClientSession, key: str) -> dict | None:
    for query in _place_queries(key):
        point = await _geocode(session, query)
        if point and _name_matches(query, point["name"]):
            return point
    return None


def _fold_place(value: str) -> str:
    """Treat Koodlu and Kudlu as the same spoken place."""
    text = value.strip().lower()
    text = text.replace("oo", "u").replace("ee", "i").replace("aa", "a")
    return re.sub(r"(.)\1+", r"\1", text)


def _name_matches(query: str, result: str) -> bool:
    """A map hit counts only when its name is the place the farmer said."""
    left = _fold_place(query)
    right = _fold_place(result)
    if not left or not right:
        return False
    if left == right or left in right or right in left:
        return True
    if skeleton(query) and skeleton(query) == skeleton(result):
        return True
    if abs(len(left) - len(right)) > 2:
        return False
    previous = list(range(len(right) + 1))
    for i, char in enumerate(left, start=1):
        current = [i]
        for j, other in enumerate(right, start=1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (char != other)))
        previous = current
    return previous[-1] <= 2


def _known_point(key: str) -> dict | None:
    name = canonical_place(key)
    coords = DISTRICT_COORDS.get(name.lower()) or DISTRICT_COORDS.get(key)
    if not coords:
        return None
    label = name if name.lower() in DISTRICT_COORDS or name in set(PLACE_ALIASES.values()) else key.title()
    return {"name": label, "lat": coords[0], "lon": coords[1]}


async def _geocode(session: aiohttp.ClientSession, query: str) -> dict | None:
    timeout = aiohttp.ClientTimeout(total=1.2)
    try:
        async with session.get(
            GEOCODE_URL,
            params={
                "q": query,
                "format": "jsonv2",
                "limit": 5,
                "countrycodes": "in",
                "addressdetails": 1,
            },
            headers={"User-Agent": _USER_AGENT},
            timeout=timeout,
        ) as response:
            if response.status >= 400:
                return None
            data = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError):
        return None
    if not isinstance(data, list) or not data:
        return None
    chosen = sorted(
        data,
        key=lambda row: (
            0 if ((row.get("address") or {}).get("state") == "Karnataka") else 1,
            0 if _name_matches(query, str(row.get("name") or "")) else 1,
            -float(row.get("importance") or 0),
        ),
    )[0]
    try:
        lat = float(chosen["lat"])
        lon = float(chosen["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    label = str(chosen.get("name") or query).strip() or query.title()
    return {"name": label, "lat": lat, "lon": lon}
