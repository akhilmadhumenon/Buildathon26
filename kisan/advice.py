"""Answers built only from the database and a weather lookup."""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from typing import Callable
from urllib.request import Request, urlopen

from kisan.db import CROP_ALIASES, DISTRICT_COORDS, KEEPING, PLACE_ALIASES, Database, canonical_place
from kisan.summary import overlap_score

WeatherLookup = Callable[[str], str]


def format_caller_context(db: Database, phone: str) -> str:
    farmer = db.get_farmer(phone)
    calls = db.recent_calls(phone, limit=5)
    lines = [f"Caller phone: {phone or 'unknown'}"]
    if farmer:
        lines.append(
            "Saved profile: "
            f"name={farmer.get('name') or 'unknown'}, "
            f"district={farmer.get('district') or 'unknown'}, "
            f"crop={farmer.get('crop') or 'unknown'}, "
            f"sown_on={farmer.get('sown_on') or 'unknown'}."
        )
    else:
        lines.append("Saved profile: none yet. Ask for district, crop, and sowing date when the question needs them.")
    if not calls:
        lines.append("Previous calls: none. This is the first call from this number. Do not claim you remember an earlier conversation.")
        return "\n".join(lines)

    for call in calls[:1]:
        summary = " ".join(str(call["summary"] or "").split())
        lines.append(f"Latest previous call: {summary[:240]}")
    return "\n".join(lines)


def related_calls(db: Database, phone: str, question: str) -> str:
    calls = db.recent_calls(phone, limit=8)
    if not calls:
        return "No earlier calls are stored for this phone number."
    ranked = sorted(
        calls,
        key=lambda call: overlap_score(question, f"{call['questions']} {call['summary']}"),
        reverse=True,
    )
    best = ranked[0]
    score = overlap_score(question, f"{best['questions']} {best['summary']}")
    if score == 0:
        return "No earlier call matches this question. Answer it on its own and do not mention past calls."
    lines = [f"Closest earlier call (overlap {score}): {best['summary']}"]
    for call in ranked[1:3]:
        extra = overlap_score(question, f"{call['questions']} {call['summary']}")
        if extra:
            lines.append(f"Also related: {call['summary']}")
    return " ".join(lines)


_CROP_SPOKEN = {
    "en": {"tomato": "Tomato", "ragi": "Ragi", "maize": "Maize", "paddy": "Paddy", "onion": "Onion", "chilli": "Chilli"},
    "hi": {"tomato": "टमाटर", "ragi": "रागी", "maize": "मक्का", "paddy": "धान", "onion": "प्याज", "chilli": "मिर्च"},
    "kn": {"tomato": "ಟೊಮೇಟೊ", "ragi": "ರಾಗಿ", "maize": "ಮೆಕ್ಕೆಜೋಳ", "paddy": "ಭತ್ತ", "onion": "ಈರುಳ್ಳಿ", "chilli": "ಮೆಣಸಿನಕಾಯಿ"},
}
_PLACE_SPOKEN = {
    "en": {"Kolar": "Kolar", "Mandya": "Mandya", "Davangere": "Davangere", "Gangavathi": "Gangavathi", "Hubballi": "Hubballi", "Bengaluru": "Bengaluru"},
    "hi": {"Kolar": "कोलार", "Mandya": "मंड्या", "Davangere": "दावणगेरे", "Gangavathi": "गंगावती", "Hubballi": "हुबली", "Bengaluru": "बेंगलुरु"},
    "kn": {"Kolar": "ಕೋಲಾರ", "Mandya": "ಮಂಡ್ಯ", "Davangere": "ದಾವಣಗೆರೆ", "Gangavathi": "ಗಂಗಾವತಿ", "Hubballi": "ಹುಬ್ಬಳ್ಳಿ", "Bengaluru": "ಬೆಂಗಳೂರು"},
}
_TREND_SPOKEN = {
    "en": {"down": "come down a little", "up": "gone up a little", "flat": "stayed steady"},
    "hi": {"down": "थोड़ा गिरा है", "up": "थोड़ा चढ़ा है", "flat": "स्थिर है"},
    "kn": {"down": "ಸ್ವಲ್ಪ ಇಳಿದಿದೆ", "up": "ಸ್ವಲ್ಪ ಏರಿದೆ", "flat": "ಸ್ಥಿರವಾಗಿದೆ"},
}


GOODBYE = {
    "en": "Alright, take care.",
    "hi": "ठीक है, अपना ध्यान रखिए।",
    "kn": "ಸರಿ, ಜೋಪಾನವಾಗಿರಿ.",
}


def _parts(earlier: str | list[str]) -> list[str]:
    if isinstance(earlier, str):
        return [earlier] if earlier.strip() else []
    return [part for part in earlier if part and part.strip()]


def _recent(question: str, earlier: str | list[str], aliases: dict[str, str]) -> list[str]:
    """The crop or mandi in this sentence, or the latest one from earlier turns."""
    found = _mentioned(question, aliases)
    if found:
        return found
    for text in reversed(_parts(earlier)):
        previous = _mentioned(text, aliases)
        if len(previous) == 1:
            return previous
    return []


def _recent_places(question: str, earlier: str | list[str]) -> list[str]:
    """Same as _recent for mandis, ignoring Mandya when the word is really mandi."""
    found = _without_false_mandya(question, _mentioned(question, PLACE_ALIASES))
    if found:
        return found
    for text in reversed(_parts(earlier)):
        previous = _without_false_mandya(text, _mentioned(text, PLACE_ALIASES))
        if len(previous) == 1:
            return previous
    return []


def _without_false_mandya(question: str, places: list[str]) -> list[str]:
    """ಮಂಡ್ಯದಲ್ಲಿ is often the word mandi, not the town Mandya, when another place is named."""
    if "Mandya" not in places or len(places) < 2:
        return places
    heard_as_mandi = (
        "ಮಂಡ್ಯದಲ್ಲಿ" in question
        or "ಮಂಡಿಯಲ್ಲಿ" in question
        or re.search(r"mandya(?=\u0CA6\u0CB2\u0CCD\u0CB2\u0CBF)", question, re.IGNORECASE)
    )
    if heard_as_mandi:
        return [place for place in places if place != "Mandya"]
    return places


def spoken_mandi(
    db: Database,
    question: str,
    language: str,
    phone: str = "",
    earlier: str | list[str] = "",
) -> str:
    """A short spoken reply with no model call.

    A clear crop and mandi gets the price. One of the two gets a short question.
    A follow-up that only names the missing piece uses what the caller already said.
    """
    crops = _recent(question, earlier, CROP_ALIASES)
    places = _recent_places(question, earlier)
    language = language if language in _CROP_SPOKEN else "en"
    if _is_closing(question):
        return GOODBYE[language]
    polite = _polite_reply(question, language)
    if polite:
        return polite
    resumed = _resume_pending_place(db, phone, question, earlier, language)
    if resumed:
        return resumed
    saved = _remember_caller_place(db, phone, question, places, crops, language)
    if saved:
        return saved
    if _is_nearest_question(question) and not _is_sale_question(question):
        origin = places[0] if _place_in_question(question) else _stored_place(db, phone)
        if not origin:
            return _ask_caller_place(language)
        return speak_nearest(
            db,
            phone,
            crops[0] if len(crops) == 1 else "",
            origin,
            language,
        )
    if _is_sale_question(question) and len(crops) == 1:
        origin = places[0] if _place_in_question(question) else _stored_place(db, phone)
        if not origin:
            return _ask_caller_place(language)
        if phone and not _stored_place(db, phone):
            db.save_farmer(phone, district=origin)
        plan = speak_sale(db, crops[0], origin, language, question)
        if plan:
            return plan
    if len(crops) == 1 and len(places) == 1:
        rows = _observed(db.mandi_history(crops[0], places[0]))
        named_place = places[0]
        if not rows:
            only = _only_mandi(db, crops[0])
            if not only:
                return ""
            rows = _observed(db.mandi_history(crops[0], only))
            named_place = only
        if not rows:
            return ""
        latest = rows[0]
        crop = _CROP_SPOKEN[language].get(latest["crop"], latest["crop"])
        place = _PLACE_SPOKEN[language].get(latest["mandi"], latest["mandi"])
        price = f"{int(latest['price_per_quintal']):,}"
        moved = named_place != places[0]
        if language == "hi":
            if moved:
                return f"जी हाँ! {crop} {place} मंडी में है, {price} रुपये क्विंटल!"
            return f"जी हाँ! {place} में {crop} {price} रुपये क्विंटल है!"
        if language == "kn":
            if moved:
                return f"ಹೌದು! {crop} {place} ಮಂಡಿಯಲ್ಲಿದೆ, {price} ರೂಪಾಯಿ ಕ್ವಿಂಟಲ್!"
            return f"ಹೌದು! {place}ನಲ್ಲಿ {crop} {price} ರೂಪಾಯಿ ಕ್ವಿಂಟಲ್!"
        if moved:
            return f"Yes! {crop} is in {place}, at {price} rupees a quintal!"
        return f"Yes! {crop} in {place} is {price} rupees a quintal."
    if len(crops) == 1 and not places:
        return {"en": "Of course! Which mandi?", "hi": "जी हाँ! कौन सी मंडी?", "kn": "ಹೌದು! ಯಾವ ಮಂಡಿ?"}[language]
    if len(places) == 1 and not crops:
        return {"en": "Of course! Which crop?", "hi": "जी हाँ! कौन सी फसल?", "kn": "ಹೌದು! ಯಾವ ಬೆಳೆ?"}[language]
    return ""


def _is_sale_question(question: str) -> bool:
    """True when the caller is deciding whether and where to sell, not just asking the price."""
    text = (question or "").lower()
    hints = (
        "sell", "selling", "wait", "week", "right time", "good time",
        "बेच", "इंतजार", "इंतज़ार", "हफ्ता", "सही समय",
        "ಮಾರಾಟ", "ಮಾರಿ", "ಕಾಯ", "ವಾರ", "ಸರಿಯಾದ",
    )
    return any(hint in text for hint in hints)


def _is_nearest_question(question: str) -> bool:
    text = (question or "").lower()
    hints = (
        "nearest", "closest", "near me", "best price", "best deal", "best mandi",
        "सबसे पास", "नजदीक", "सबसे अच्छा",
        "ಹತ್ತಿರ", "ಉತ್ತಮ ಬೆಲೆ",
    )
    return any(hint in text for hint in hints)


def _stored_place(db: Database, phone: str) -> str:
    if not phone:
        return ""
    farmer = db.get_farmer(phone) or {}
    return str(farmer.get("district") or "")


def _ask_caller_place(language: str) -> str:
    return {
        "en": "Of course! Which place are you calling from?",
        "hi": "जी हाँ! आप कहाँ से बोल रहे हैं?",
        "kn": "ಹೌದು! ನೀವು ಎಲ್ಲಿಂದ ಕರೆ ಮಾಡುತ್ತಿದ್ದೀರಿ?",
    }[language]


def _place_in_question(question: str) -> bool:
    return len(_recent_places(question, [])) == 1


def _question_needing_place(earlier: str | list[str]) -> str:
    parts = _parts(earlier)
    if not parts:
        return ""
    text = parts[-1]
    needs = _is_sale_question(text) or _is_nearest_question(text)
    if needs and not _place_in_question(text):
        return text
    return ""


def _resume_pending_place(
    db: Database,
    phone: str,
    question: str,
    earlier: str | list[str],
    language: str,
) -> str:
    """The caller just named a place so an earlier sell or nearest question can be answered."""
    pending = _question_needing_place(earlier)
    named = _recent_places(question, [])
    if not pending or len(named) != 1 or _mentioned(question, CROP_ALIASES):
        return ""
    if _is_sale_question(question) or _is_nearest_question(question):
        return ""
    if phone:
        db.save_farmer(phone, district=named[0])
    if _is_sale_question(pending):
        crops = _mentioned(pending, CROP_ALIASES)
        if len(crops) == 1:
            return speak_sale(db, crops[0], named[0], language, pending)
    if _is_nearest_question(pending):
        crops = _mentioned(pending, CROP_ALIASES)
        return speak_nearest(db, phone, crops[0] if len(crops) == 1 else "", named[0], language)
    return ""


def _remember_caller_place(
    db: Database,
    phone: str,
    question: str,
    places: list[str],
    crops: list[str],
    language: str,
) -> str:
    """Save the first place the caller names, unless this sentence is already a full question."""
    if not phone or len(places) != 1 or crops or _stored_place(db, phone):
        return ""
    if _is_sale_question(question) or _is_nearest_question(question):
        return ""
    db.save_farmer(phone, district=places[0])
    place = _place_word(language, places[0])
    return {
        "en": f"Yes! {place} is saved. Go ahead!",
        "hi": f"जी हाँ! {place} दर्ज हो गया। बोलिए!",
        "kn": f"ಹೌದು! {place} ದಾಖಲಾಗಿದೆ. ಹೇಳಿ!",
    }[language]


def speak_nearest(db: Database, phone: str, crop: str, place: str, language: str) -> str:
    """Nearest priced mandi to the caller, and a better price when another mandi pays more."""
    language = language if language in _CROP_SPOKEN else "en"
    origin = place or _stored_place(db, phone)
    if not origin:
        return _ask_caller_place(language)
    if origin.lower() not in DISTRICT_COORDS:
        return {
            "en": f"Yes! I do not have a map point for {origin} yet. Please name the district.",
            "hi": f"जी हाँ! {origin} का नक्शा अभी नहीं है। जिला बताइए!",
            "kn": f"ಹೌದು! {origin} ನಕ್ಷೆ ಇನ್ನೂ ಇಲ್ಲ. ಜಿಲ್ಲೆ ಹೇಳಿ!",
        }[language]
    quotes = [
        row for row in db.latest_prices(crop)
        if row["mandi"].lower() in DISTRICT_COORDS
    ]
    if not quotes:
        crop_word = _crop_word(language, crop, crop) if crop else ""
        return {
            "en": f"Yes! I do not have a {crop_word or 'mandi'} price on record yet.",
            "hi": f"जी हाँ! {crop_word or 'मंडी'} का भाव अभी दर्ज नहीं है!",
            "kn": f"ಹೌದು! {crop_word or 'ಮಂಡಿ'} ಬೆಲೆ ಇನ್ನೂ ದಾಖಲಾಗಿಲ್ಲ!",
        }[language]
    nearest = min(quotes, key=lambda row: _km(origin, row["mandi"]))
    same_crop = [row for row in quotes if row["crop"] == nearest["crop"]]
    best = max(same_crop, key=lambda row: int(row["price_per_quintal"]))
    if crop:
        best = max(quotes, key=lambda row: int(row["price_per_quintal"]))
    return _speak_nearest(language, origin, nearest, best)


def _speak_nearest(language: str, origin: str, nearest: dict, best: dict) -> str:
    origin_word = _place_word(language, origin)
    near_word = _place_word(language, nearest["mandi"])
    near_crop = _CROP_SPOKEN[language].get(nearest["crop"], nearest["crop"])
    near_price = f"{int(nearest['price_per_quintal']):,}"
    distance = int(round(_km(origin, nearest["mandi"])))
    same_place = nearest["mandi"].lower() == origin.lower() or distance < 1
    better = best["mandi"] != nearest["mandi"] and int(best["price_per_quintal"]) > int(nearest["price_per_quintal"])
    best_word = _place_word(language, best["mandi"])
    best_crop = _CROP_SPOKEN[language].get(best["crop"], best["crop"])
    best_price = f"{int(best['price_per_quintal']):,}"
    best_distance = int(round(_km(origin, best["mandi"])))
    if language == "hi":
        where = f"{near_word} में ही" if same_place else f"{near_word}, करीब {distance} किलोमीटर"
        line = f"जी हाँ! {origin_word} से सबसे पास मंडी {where} है। {near_crop} {near_price} रुपये क्विंटल है!"
        if better:
            line += f" बेहतर भाव {best_word} में है, {best_crop} {best_price} रुपये, करीब {best_distance} किलोमीटर!"
        return line
    if language == "kn":
        where = f"{near_word}ನಲ್ಲೇ" if same_place else f"{near_word}, ಸುಮಾರು {distance} ಕಿಲೋಮೀಟರ್"
        line = f"ಹೌದು! {origin_word}ಗೆ ಹತ್ತಿರದ ಮಂಡಿ {where}. {near_crop} {near_price} ರೂಪಾಯಿ ಕ್ವಿಂಟಲ್!"
        if better:
            line += f" ಉತ್ತಮ ಬೆಲೆ {best_word}ನಲ್ಲಿ, {best_crop} {best_price} ರೂಪಾಯಿ, ಸುಮಾರು {best_distance} ಕಿಲೋಮೀಟರ್!"
        return line
    where = f"{near_word} itself" if same_place else f"{near_word}, about {distance} kilometres away"
    line = (
        f"Yes! The nearest mandi to {origin_word} is {where}. "
        f"{near_crop} there is {near_price} rupees a quintal!"
    )
    if better:
        line += (
            f" The better price is at {best_word}, {best_crop} at {best_price} rupees,"
            f" about {best_distance} kilometres away!"
        )
    return line


def _crop_word(language: str, crop: str, question: str) -> str:
    if language == "en" and re.search(r"\brice\b", question or "", re.IGNORECASE):
        return "Rice"
    return _CROP_SPOKEN[language].get(crop, crop)


def _place_word(language: str, place: str) -> str:
    return _PLACE_SPOKEN.get(language, {}).get(place, place)


def _km(origin: str, mandi: str) -> float:
    start = DISTRICT_COORDS.get((origin or "").lower())
    end = DISTRICT_COORDS.get((mandi or "").lower())
    if not start or not end:
        return 1e9
    return _km_between(start[0], start[1], end[0], end[1])


def _km_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1 = math.radians(lat1), math.radians(lon1)
    lat2, lon2 = math.radians(lat2), math.radians(lon2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    arc = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(arc))


def _closest(origin: str, mandis: list[str]) -> str:
    if not mandis:
        return ""
    if origin and origin.lower() in DISTRICT_COORDS:
        return min(mandis, key=lambda name: _km(origin, name))
    return mandis[0]


def _series_for(db: Database, crop: str) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in db.crop_history(crop):
        grouped.setdefault(row["mandi"], []).append(row)
    return grouped


def _observed(rows: list[dict]) -> list[dict]:
    """Prices on or before today. A forecast date is not the price a caller would get now."""
    today = date.today().isoformat()
    kept = [row for row in rows if row["recorded_on"] <= today]
    return kept or rows


def _now_and_week(rows: list[dict]) -> tuple[int, int]:
    """Today's price, and the price a week ahead if the recent change continues."""
    rows = _observed(rows)
    now = int(rows[0]["price_per_quintal"])
    if len(rows) < 2:
        return now, now
    oldest = rows[-1]
    days = (date.fromisoformat(rows[0]["recorded_on"]) - date.fromisoformat(oldest["recorded_on"])).days
    if days <= 0:
        return now, now
    daily = (now - int(oldest["price_per_quintal"])) / days
    return now, max(0, int(round(now + daily * 7)))


def speak_sale(db: Database, crop: str, place: str, language: str, question: str = "") -> str:
    """A sell-or-wait plan from stored prices and distance. No crop has its own script."""
    language = language if language in _CROP_SPOKEN else "en"
    series = _series_for(db, crop)
    crop_word = _crop_word(language, crop, question)
    if not series:
        return {
            "en": f"Yes! I do not have a {crop_word} price on record yet.",
            "hi": f"जी हाँ! {crop_word} का भाव अभी दर्ज नहीं है!",
            "kn": f"ಹೌದು! {crop_word} ಬೆಲೆ ಇನ್ನೂ ದಾಖಲಾಗಿಲ್ಲ!",
        }[language]
    missing = bool(place) and place not in series
    anchor = place if place in series else _closest(place, list(series))
    now, week = _now_and_week(series[anchor])
    others = [name for name in series if name != anchor]
    neighbour = _closest(place or anchor, others)
    neighbour_now = _now_and_week(series[neighbour])[0] if neighbour else None
    keeps = KEEPING.get(crop, (7, ""))[0]
    options: list[tuple[str, str, int]] = [("now", anchor, now)]
    if keeps >= 7:
        options.append(("week", anchor, week))
    if neighbour and neighbour_now is not None:
        options.append(("nearby", neighbour, neighbour_now))
    rank = {"now": 0, "week": 1, "nearby": 2}
    choice, choice_place, _choice_price = max(options, key=lambda item: (item[2], -rank[item[0]]))
    return _speak_sale_choice(
        language,
        crop_word,
        place,
        anchor,
        now,
        week,
        neighbour,
        neighbour_now,
        missing,
        choice,
        choice_place,
    )


def _speak_sale_choice(
    language: str,
    crop: str,
    asked: str,
    anchor: str,
    now: int,
    week: int,
    neighbour: str,
    neighbour_now: int | None,
    missing: bool,
    choice: str,
    choice_place: str,
) -> str:
    anchor_word = _place_word(language, anchor)
    asked_word = _place_word(language, asked) if asked else anchor_word
    neighbour_word = _place_word(language, neighbour) if neighbour else ""
    now_text = f"{now:,}"
    week_text = f"{week:,}"
    other_text = f"{neighbour_now:,}" if neighbour_now is not None else ""
    if language == "hi":
        opening = (
            f"जी हाँ! {asked_word} में {crop} का भाव दर्ज नहीं है। सबसे पास {anchor_word} मंडी है, आज {now_text} रुपये क्विंटल!"
            if missing
            else f"जी हाँ! {anchor_word} में {crop} आज {now_text} रुपये क्विंटल है!"
        )
        change = f"एक हफ्ते में हाल के बदलाव से करीब {week_text} होगा!"
        other = (
            f"सबसे पास दूसरी मंडी {neighbour_word} है, आज {other_text}!"
            if neighbour
            else f"किसी और मंडी में {crop} का भाव दर्ज नहीं है!"
        )
        action = {
            "now": f"{anchor_word} में अभी बेचिए!",
            "week": f"एक हफ्ता रुकिए, फिर {anchor_word} में बेचिए!",
            "nearby": f"{_place_word(language, choice_place)} में अभी बेचिए, वहाँ भाव ज्यादा है!",
        }[choice]
    elif language == "kn":
        opening = (
            f"ಹೌದು! {asked_word}ನಲ್ಲಿ {crop} ಬೆಲೆ ಇಲ್ಲ. ಹತ್ತಿರದ ಮಂಡಿ {anchor_word}, ಇಂದು {now_text} ರೂಪಾಯಿ ಕ್ವಿಂಟಲ್!"
            if missing
            else f"ಹೌದು! {anchor_word}ನಲ್ಲಿ {crop} ಇಂದು {now_text} ರೂಪಾಯಿ ಕ್ವಿಂಟಲ್!"
        )
        change = f"ಒಂದು ವಾರದಲ್ಲಿ ಇತ್ತೀಚಿನ ಬದಲಾವಣೆಯಿಂದ ಸುಮಾರು {week_text}!"
        other = (
            f"ಹತ್ತಿರದ ಇನ್ನೊಂದು ಮಂಡಿ {neighbour_word}, ಇಂದು {other_text}!"
            if neighbour
            else f"ಬೇರೆ ಮಂಡಿಯಲ್ಲಿ {crop} ಬೆಲೆ ಇಲ್ಲ!"
        )
        action = {
            "now": f"{anchor_word}ನಲ್ಲಿ ಈಗ ಮಾರಿ!",
            "week": f"ಒಂದು ವಾರ ಕಾಯಿರಿ, ನಂತರ {anchor_word}ನಲ್ಲಿ ಮಾರಿ!",
            "nearby": f"{_place_word(language, choice_place)}ನಲ್ಲಿ ಈಗ ಮಾರಿ, ಅಲ್ಲಿ ಬೆಲೆ ಹೆಚ್ಚು!",
        }[choice]
    else:
        opening = (
            f"Yes! {asked_word} has no {crop} price on record. The nearest mandi is {anchor_word}, at {now_text} rupees a quintal today."
            if missing
            else f"Yes! {crop} in {anchor_word} is {now_text} rupees a quintal today."
        )
        change = f"A week from now the recent change points to about {week_text}."
        other = (
            f"The nearest other mandi is {neighbour_word}, at {other_text} today."
            if neighbour
            else f"No other mandi has {crop} on record."
        )
        action = {
            "now": f"Sell at {anchor_word} now!",
            "week": f"Wait a week, then sell at {anchor_word}!",
            "nearby": f"Sell at {_place_word(language, choice_place)} now. That price is higher!",
        }[choice]
    return " ".join((opening, change, other, action))


def _only_mandi(db: Database, crop: str) -> str:
    mandis = []
    prefix = f"{crop} at "
    for market in db.known_markets():
        if market.startswith(prefix):
            mandis.append(market[len(prefix):])
    if len(set(mandis)) == 1:
        return mandis[0]
    return ""


def _is_closing(question: str) -> bool:
    """True when the caller is done, so we say goodbye instead of offering more."""
    if _mentioned(question, CROP_ALIASES) or _mentioned(question, PLACE_ALIASES):
        return False
    text = (question or "").lower()
    if re.search(r"\b(no|nope|nah|nothing|bye|goodbye)\b|that's all|thats all|nothing else|all good", text):
        return True
    if any(phrase in text for phrase in ("नहीं", "नही", "बस", "कुछ नहीं", "अलविदा", "ಇಲ್ಲ", "ಬೇಡ", "ಸಾಕು")):
        return True
    words = re.findall(r"[a-zA-Z\u0900-\u097F\u0C80-\u0CFF]+", text)
    thanks = {"thank", "thanks", "you", "so", "much", "very", "okay", "ok", "ಧನ್ಯವಾದ", "धन्यवाद", "शुक्रिया"}
    return bool(words) and any(word in {"thank", "thanks", "धन्यवाद", "शुक्रिया", "ಧನ್ಯವಾದ"} for word in words) and all(
        word in thanks for word in words
    )


def _polite_reply(question: str, language: str) -> str:
    words = re.findall(r"[a-zA-Z\u0900-\u097F\u0C80-\u0CFF]+", (question or "").lower())
    acks = {
        "sir", "madam", "सर", "साहब", "जी", "ಸರ್",
        "hello", "hi", "hey", "namaste", "namaskar", "namaskara",
        "नमस्ते", "नमस्कार", "हेलो", "ನಮಸ್ಕಾರ", "ನಮಸ್ತೆ", "ಹಲೋ",
    }
    if words and all(word in acks for word in words):
        return {"en": "Yes! Go ahead.", "hi": "जी हाँ! बोलिए।", "kn": "ಹೌದು! ಹೇಳಿ."}[language]
    return ""


_NOISE = {
    "uh", "um", "hmm", "ah", "oh", "er", "eh", "the", "a", "an",
    "that's", "that", "its", "it's", "ok", "okay", "yeah", "yes",
}


def should_answer(text: str) -> bool:
    """Skip echoes and half-words so the bot does not talk over itself."""
    words = re.findall(r"[a-zA-Z\u0900-\u097F\u0C80-\u0CFF]+", (text or "").lower())
    useful = [word for word in words if word not in _NOISE]
    if not useful:
        return False
    if any(any(ord(char) > 127 for char in word) for word in useful):
        return True
    if any(
        word in {
            "thank", "thanks", "sir", "madam", "no", "nothing", "bye", "goodbye",
            "hello", "hi", "hey", "namaste", "namaskar", "namaskara",
        }
        for word in useful
    ):
        return True
    if len(useful) >= 2:
        return True
    token = useful[0]
    if _mentioned(token, CROP_ALIASES) or _mentioned(token, PLACE_ALIASES):
        return True
    return len(token) >= 4


def facts_for_question(db: Database, question: str) -> str:
    """Return a price fact when the question already names one crop and one mandi.

    That skips a second model round-trip on the common price question.
    """
    crops = _mentioned(question, CROP_ALIASES)
    places = _mentioned(question, PLACE_ALIASES)
    if len(crops) != 1 or len(places) != 1:
        return ""
    return mandi_answer(db, crops[0], places[0])


def _mentioned(question: str, aliases: dict[str, str]) -> list[str]:
    text = (question or "").lower()
    found: list[str] = []
    for alias in sorted(aliases, key=len, reverse=True):
        if not _has_alias(text, alias):
            continue
        name = aliases[alias]
        if name not in found:
            found.append(name)
    if found:
        return found
    for word in re.findall(r"[a-z]{4,}", text):
        name = _close_alias(word, aliases)
        if name and name not in found:
            found.append(name)
    return found


def _close_alias(word: str, aliases: dict[str, str]) -> str:
    """Match a misheard place or crop when one letter changed, like Kolur for Kolar."""
    best = ""
    hits = 0
    for alias, name in aliases.items():
        if len(alias) != len(word) or len(alias) < 4 or any(ord(char) > 127 for char in alias):
            continue
        if sum(left != right for left, right in zip(alias, word)) != 1:
            continue
        if name != best:
            hits += 1
            best = name
    if hits == 1:
        return best
    return ""


def _has_alias(text: str, alias: str) -> bool:
    if any(ord(char) > 127 for char in alias):
        return alias in text
    return re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", text) is not None


def mandi_answer(db: Database, crop: str, place: str) -> str:
    rows = _observed(db.mandi_history(crop, place))
    if not rows:
        known = ", ".join(db.known_markets()) or "none loaded"
        return f"No price is stored for {crop} at {place}. Known demo markets: {known}."
    latest = rows[0]
    older = rows[-1] if len(rows) > 1 else None
    change = ""
    if older and older["id"] != latest["id"]:
        delta = latest["price_per_quintal"] - older["price_per_quintal"]
        direction = "up" if delta > 0 else "down" if delta < 0 else "unchanged"
        change = (
            f" Since {older['recorded_on']} the price is {direction} "
            f"by {abs(delta)} rupees per quintal."
        )
    return (
        f"{latest['crop']} at {latest['mandi']} mandi on {latest['recorded_on']}: "
        f"{latest['price_per_quintal']} rupees per quintal. "
        f"The stored trend is {latest['trend']}.{change}"
    )


def harvest_answer(
    db: Database,
    *,
    crop: str,
    district: str,
    sown_on: str,
    today: date | None = None,
    weather_lookup: WeatherLookup | None = None,
) -> str:
    guide = db.crop_guide(crop)
    if guide is None:
        return (
            f"No crop calendar is stored for {crop}. "
            "Known demo crops are ragi, tomato, maize, paddy, onion, chilli, banana, mango, and grapes."
        )
    sown = _parse_date(sown_on)
    if sown is None:
        return "The sowing date could not be read. Ask the caller for the date as day, month, and year."
    today = today or datetime.now().date()
    age = (today - sown).days
    if age < 0:
        return "The sowing date is in the future. Ask the caller to repeat the date."
    remaining = guide["days_to_harvest"] - age
    if remaining > 10:
        timing = (
            f"It is day {age} of about {guide['days_to_harvest']}. "
            f"About {remaining} days are left, so this is too early to harvest."
        )
    elif remaining > 0:
        timing = (
            f"It is day {age} of about {guide['days_to_harvest']}. "
            f"Harvest is close, about {remaining} days left."
        )
    else:
        timing = (
            f"It is day {age}, past the usual {guide['days_to_harvest']} days. "
            "The crop is due for harvest if the field is dry."
        )
    lookup = weather_lookup or rain_forecast
    try:
        weather = lookup(district)
    except Exception as exc:  # network or parse failure should not drop the crop answer
        weather = f"Weather could not be checked ({exc})."
    return f"{guide['crop']} in {canonical_place(district) or district}. {timing} {guide['harvest_notes']} {weather}"


def rain_forecast(district: str) -> str:
    coords = DISTRICT_COORDS.get(canonical_place(district).lower()) or DISTRICT_COORDS.get(
        (district or "").strip().lower()
    )
    if not coords:
        return "No weather point is mapped for that district in this demo."
    lat, lon = coords
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&daily=precipitation_sum&forecast_days=3&timezone=Asia%2FKolkata"
    )
    request = Request(url, headers={"User-Agent": "kisan-call/0.1"})
    with urlopen(request, timeout=4) as response:
        payload = json.loads(response.read().decode("utf-8"))
    days = payload.get("daily", {})
    dates = days.get("time") or []
    rain = days.get("precipitation_sum") or []
    if not dates:
        return "The weather service returned no forecast."
    parts = [f"{dates[i]}: {rain[i]} mm" for i in range(min(len(dates), len(rain)))]
    wet = [amount for amount in rain if isinstance(amount, (int, float)) and amount >= 5]
    advice = (
        "More than 5 mm of rain is expected, so wait for a dry day if the crop is ready to cut."
        if wet
        else "No heavy rain is expected in the next three days."
    )
    return "Rain forecast " + ", ".join(parts) + ". " + advice


def market_facts(db: Database, phone: str = "", caller_point: dict | None = None) -> str:
    """Prices, forecasts, and distances. The model decides what to say."""
    origin = _stored_place(db, phone)
    today = date.today().isoformat()
    latest = db.latest_prices()
    lines = [
        format_caller_context(db, phone),
        f"Today is {today}. Dates after today are forecasts.",
    ]
    buying: dict[str, list[str]] = {}
    for row in latest:
        buying.setdefault(row["mandi"], []).append(row["crop"])
    lines.append("What each mandi buys:")
    for mandi, crops in sorted(buying.items()):
        coords = DISTRICT_COORDS.get(mandi.lower())
        where = f" ({coords[0]}, {coords[1]})" if coords else ""
        lines.append(f"- {mandi}{where}: {', '.join(sorted(crops))}")
    mandis = [name for name in buying if name.lower() in DISTRICT_COORDS]
    if caller_point and caller_point.get("lat") is None:
        lines.append(
            f"Caller said {caller_point['name']}. The map did not find it. "
            "Ask them to say the place once more."
        )
    elif caller_point:
        lines.append(
            f"Caller map point: {caller_point['name']} at "
            f"{caller_point['lat']:.4f}, {caller_point['lon']:.4f}."
        )
        ranked = []
        for mandi in mandis:
            coords = DISTRICT_COORDS[mandi.lower()]
            kilometres = _km_between(
                caller_point["lat"], caller_point["lon"], coords[0], coords[1]
            )
            ranked.append((kilometres, mandi))
        ranked.sort()
        lines.append(f"Distance from {caller_point['name']}:")
        for kilometres, mandi in ranked:
            crops = ", ".join(sorted(buying[mandi]))
            lines.append(
                f"- {caller_point['name']} to {mandi}: {int(round(kilometres))} km. Buys {crops}."
            )
        if ranked:
            kilometres, mandi = ranked[0]
            lines.append(
                f"Closest mandi to {caller_point['name']} is {mandi}, {int(round(kilometres))} km."
            )
    else:
        lines.append("Caller place: " + (origin or "unknown") + ".")
    if len(mandis) > 1:
        pairs = [
            f"{left}-{right} {int(round(_km(left, right)))}"
            for left_index, left in enumerate(mandis)
            for right in mandis[left_index + 1 :]
        ]
        lines.append("Kilometres between mandis: " + ", ".join(pairs) + ".")
    for row in latest:
        history = db.mandi_history(row["crop"], row["mandi"]) or [row]
        observed = [item for item in history if item["recorded_on"] <= today]
        forecast = [item for item in history if item["recorded_on"] > today]
        now, week = _now_and_week(observed or history)
        ahead = ",".join(str(item["price_per_quintal"]) for item in reversed(forecast))
        lines.append(
            f"{row['crop']} {row['mandi']}: today {now}. Forecast {ahead}. Week {week}."
        )
    if KEEPING:
        kept = ". ".join(
            f"{crop} {days} days {note}" for crop, (days, note) in sorted(KEEPING.items())
        )
        lines.append("How long the harvest keeps: " + kept + ".")
    return "\n".join(lines)


def _parse_date(value: str) -> date | None:
    text = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None
