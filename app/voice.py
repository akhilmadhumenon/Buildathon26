"""Spoken answers for the phone agent.

The call path only reads cards already built in memory. It does not fetch
Agmarknet, Open-Meteo, or the Kisan Call Centre. The response shape is fixed
in docs/VOICE.md. The phone agent speaks `say` verbatim.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from app.calls import normalize_phone
from app.crops import ALIASES, CALL_WINDOW_DAYS, CROPS, PLACES, canonical_crop, crop_name
from app.forecast import snapshot
from app.kcc import ground
from app.sms import render_sms
from app.weather import rain_outlook

LANGUAGES = ("en", "hi", "kn")

_NAMES: list[tuple[str, str]] = []
for _crop, _row in CROPS.items():
    for _lang in LANGUAGES:
        _NAMES.append((_row[_lang], _crop))
for _alias, _crop in ALIASES.items():
    _NAMES.append((_alias, _crop))
_NAMES.sort(key=lambda item: len(item[0]), reverse=True)

UNKNOWN_CROPS = (
    "mango", "grape", "grapes", "coffee", "arecanut", "areca",
    "coconut", "sugarcane", "cotton", "आम", "अंगूर", "ಮಾವಿನ", "ದ್ರಾಕ್ಷಿ",
)
OTHER_PLACES = ("mandya", "gangavathi", "gangavati")
DOSE_WORDS = (
    "dose", "pesticide", "fertilizer", "fertiliser", "urea", "spray",
    "खाद", "कीटनाशक", "दवाई", "ಗೊಬ್ಬರ", "ಕೀಟನಾಶಕ",
)
RAIN_WORDS = ("rain", "weather", "barish", "baarish", "बारिश", "मौसम", "ಮಳೆ", "ಹವಾಮಾನ")
HARVEST_WORDS = ("harvest", "तुड़ाई", "कटाई", "कटाई", "ಕೊಯ್ಲು")
SALE_WORDS = ("sell", "wait", "hold", "बेच", "रुक", "ಮಾರಾಟ", "ತಡೆ")

ASK = {
    "crop": {
        "en": "Which crop? This line covers tomato, onion, potato, dry chilli, banana, ragi, maize, tur, paddy, and groundnut.",
        "hi": "कौन सी फसल? इस लाइन पर टमाटर, प्याज, आलू, सूखी मिर्च, केला, रागी, मक्का, तुअर, धान और मूँगफली हैं.",
        "kn": "ಯಾವ ಬೆಳೆ? ಈ ಸಾಲಿನಲ್ಲಿ ಟೊಮೇಟೊ, ಈರುಳ್ಳಿ, ಆಲೂಗಡ್ಡೆ, ಒಣ ಮೆಣಸಿನಕಾಯಿ, ಬಾಳೆಹಣ್ಣು, ರಾಗಿ, ಮೆಕ್ಕೆಜೋಳ, ತೊಗರಿ, ಭತ್ತ ಮತ್ತು ಕಡಲೆಕಾಯಿ ಇವೆ.",
    },
    "sowing": {
        "en": "When was this crop sown? Say the date, and I will read the harvest window.",
        "hi": "यह फसल कब बोई गई? तारीख बताइए, तब कटाई की अवधि बताऊँगा.",
        "kn": "ಈ ಬೆಳೆ ಯಾವಾಗ ಬಿತ್ತಿದಿರಿ? ದಿನಾಂಕ ಹೇಳಿ, ಆಗ ಕೊಯ್ಲು ಅವಧಿ ಹೇಳುತ್ತೇನೆ.",
    },
    "place": {
        "en": "Which district? This line has a mandi series for its ten crops only.",
        "hi": "कौन सा जिला? इस लाइन पर सिर्फ दस फसलों की मंडी है.",
        "kn": "ಯಾವ ಜಿಲ್ಲೆ? ಈ ಸಾಲಿನಲ್ಲಿ ಹತ್ತು ಬೆಳೆಗಳ ಮಂಡಿ ಮಾತ್ರ ಇದೆ.",
    },
}

REFUSAL = {
    "dose": {
        "en": "RaithaLine does not give a pesticide or fertilizer dose unless a Kisan Call Centre record matches. Confirm any dose with the nearest Krishi Vigyan Kendra.",
        "hi": "किसान कॉल सेंटर का मेल न हो तो राइथलाइन खाद या दवाई की मात्रा नहीं बताती. मात्रा के लिए नजदीकी कृषि विज्ञान केंद्र से पूछें.",
        "kn": "ಕಿಸಾನ್ ಕಾಲ್ ಸೆಂಟರ್ ದಾಖಲೆ ಸಿಗದಿದ್ದರೆ ರೈತಲೈನ್ ಗೊಬ್ಬರ ಅಥವಾ ಔಷಧದ ಪ್ರಮಾಣ ಹೇಳುವುದಿಲ್ಲ. ಹತ್ತಿರದ ಕೃಷಿ ವಿಜ್ಞಾನ ಕೇಂದ್ರದಲ್ಲಿ ಖಚಿತಪಡಿಸಿ.",
    },
    "unknown_crop": {
        "en": "That crop is not on this line. It covers tomato, onion, potato, dry chilli, banana, ragi, maize, tur, paddy, and groundnut.",
        "hi": "वह फसल इस लाइन पर नहीं है. टमाटर, प्याज, आलू, सूखी मिर्च, केला, रागी, मक्का, तुअर, धान और मूँगफली हैं.",
        "kn": "ಆ ಬೆಳೆ ಈ ಸಾಲಿನಲ್ಲಿಲ್ಲ. ಟೊಮೇಟೊ, ಈರುಳ್ಳಿ, ಆಲೂಗಡ್ಡೆ, ಒಣ ಮೆಣಸಿನಕಾಯಿ, ಬಾಳೆಹಣ್ಣು, ರಾಗಿ, ಮೆಕ್ಕೆಜೋಳ, ತೊಗರಿ, ಭತ್ತ ಮತ್ತು ಕಡಲೆಕಾಯಿ ಇವೆ.",
    },
    "missing_series": {
        "en": "There is no mandi series for that crop at that place, so this line will not guess a price.",
        "hi": "उस जगह उस फसल की मंडी श्रृंखला नहीं है, इसलिए यह लाइन भाव नहीं गढ़ेगी.",
        "kn": "ಆ ಊರಿನಲ್ಲಿ ಆ ಬೆಳೆಗೆ ಮಂಡಿ ಸರಣಿ ಇಲ್ಲ, ಆದ್ದರಿಂದ ಈ ಸಾಲು ಬೆಲೆ ಊಹಿಸುವುದಿಲ್ಲ.",
    },
    "out_of_domain": {
        "en": "That is outside this line. It can talk about the mandi price, the rain, and the crop on this farm.",
        "hi": "यह इस लाइन के बाहर है. यह मंडी भाव, बारिश और इस खेत की फसल पर बात कर सकती है.",
        "kn": "ಅದು ಈ ಸಾಲಿನ ಹೊರಗೆ. ಇದು ಮಂಡಿ ಬೆಲೆ, ಮಳೆ ಮತ್ತು ಈ ಹೊಲದ ಬೆಳೆಯ ಬಗ್ಗೆ ಮಾತ್ರ ಹೇಳುತ್ತದೆ.",
    },
    "rain_missing": {
        "en": "The rain line for that district is not ready, so this line will not guess the rainfall.",
        "hi": "उस जिले की बारिश की लाइन तैयार नहीं, इसलिए यह लाइन बारिश नहीं गढ़ेगी.",
        "kn": "ಆ ಜಿಲ್ಲೆಯ ಮಳೆ ಸಾಲು ಸಿದ್ಧವಿಲ್ಲ, ಆದ್ದರಿಂದ ಈ ಸಾಲು ಮಳೆ ಊಹಿಸುವುದಿಲ್ಲ.",
    },
}

CONFIRM = {
    "en": "Confirm this with the nearest Krishi Vigyan Kendra before you act.",
    "hi": "कुछ करने से पहले नजदीकी कृषि विज्ञान केंद्र से इस बात की पुष्टि करें.",
    "kn": "ಏನನ್ನಾದರೂ ಮಾಡುವ ಮುನ್ನ ಹತ್ತಿರದ ಕೃಷಿ ವಿಜ್ಞಾನ ಕೇಂದ್ರದಲ್ಲಿ ಇದನ್ನು ಖಚಿತಪಡಿಸಿ.",
}

RAIN = {
    "en": "Rain at {district} over the next two days is {mm} millimetres.",
    "hi": "{district} में अगले दो दिनों की बारिश {mm} मिलीमीटर है.",
    "kn": "{district} ನಲ್ಲಿ ಮುಂದಿನ ಎರಡು ದಿನದ ಮಳೆ {mm} ಮಿಲಿಮೀಟರ್.",
}
RAIN_HEAVY = {
    "en": " That is heavy rain.",
    "hi": " यह भारी बारिश है.",
    "kn": " ಇದು ಭಾರೀ ಮಳೆ.",
}
HARVEST = {
    "en": "{crop} sown on {sown}. The harvest window on this line is {start} to {end}.",
    "hi": "{crop} की बुवाई {sown}. इस लाइन पर कटाई की अवधि {start} से {end} है.",
    "kn": "{crop} ಬಿತ್ತನೆ {sown}. ಈ ಸಾಲಿನ ಕೊಯ್ಲು ಅವಧಿ {start} ರಿಂದ {end}.",
}


def build_cards(frame, tomato: dict | None, gates: dict | None) -> dict:
    """One spoken card per crop and language, from the snapshot already in memory."""
    cards: dict = {}
    gates = gates or {}
    for crop in CROPS:
        snap = snapshot(frame, crop, tomato, gates.get(crop))
        cards[crop] = {language: _price_card(snap, language) for language in LANGUAGES}
    return cards


def answer(
    *,
    cards: dict,
    weather: dict | None,
    text: str,
    language: str | None = None,
    crop: str | None = None,
    place: str | None = None,
    phone: str | None = None,
    sown_on: str | None = None,
    connection=None,
) -> dict:
    heard = (text or "").strip()
    language = _language(language, heard)
    if not heard and not crop:
        return _ask(language, "crop")
    chosen = canonical_crop(crop) or _match_crop(heard)
    if _any(heard, DOSE_WORDS):
        return _dose(language, heard, chosen, connection)
    if _any(heard, RAIN_WORDS):
        return _rain(language, weather or {}, heard, place, chosen)
    if _any(heard, HARVEST_WORDS):
        return _harvest(cards, language, heard, chosen, place, phone, sown_on, connection)
    if _unknown_crop(heard) and chosen is None:
        return _refuse(language, "unknown_crop")
    if chosen is None:
        if _looks_farming(heard):
            return _ask(language, "crop")
        return _refuse(language, "out_of_domain")
    if _place_conflicts(chosen, heard, place):
        return _refuse(language, "missing_series")
    intent = "sell" if _any(heard, SALE_WORDS) else "price"
    return _from_card(cards, chosen, language, intent)


def _price_card(snap: dict, language: str) -> dict:
    if not snap.get("available"):
        return {
            "say": REFUSAL["missing_series"][language],
            "facts": [],
            "citation": snap.get("citation"),
            "available": False,
        }
    facts = []
    if snap.get("modal") is not None:
        facts.append({"key": "modal", "value": snap["modal"], "unit": "INR/qtl", "as_of": snap.get("as_of")})
    if snap.get("min_price") is not None and snap.get("max_price") is not None:
        facts.append({"key": "min_price", "value": snap["min_price"], "unit": "INR/qtl", "as_of": snap.get("as_of")})
        facts.append({"key": "max_price", "value": snap["max_price"], "unit": "INR/qtl", "as_of": snap.get("as_of")})
    if snap.get("arrival_7d_tonnes") is not None:
        facts.append(
            {"key": "arrival_7d", "value": snap["arrival_7d_tonnes"], "unit": "tonnes", "as_of": snap.get("as_of")}
        )
    if snap.get("forecast_7d") is not None:
        facts.append({"key": "forecast_7d", "value": snap["forecast_7d"], "unit": "INR/qtl", "as_of": snap.get("as_of")})
    facts.append({"key": "action", "value": snap.get("action"), "unit": None, "as_of": snap.get("as_of")})
    return {
        "say": render_sms(snap, language),
        "facts": facts,
        "citation": snap.get("citation"),
        "available": True,
    }


def _from_card(cards: dict, crop: str, language: str, intent: str) -> dict:
    card = (cards.get(crop) or {}).get(language) or {}
    if not card or not card.get("available"):
        return _refuse(language, "missing_series")
    return _pack(
        language,
        intent,
        card["say"],
        facts=card.get("facts") or [],
        citation=card.get("citation"),
    )


def _dose(language: str, text: str, crop: str | None, connection) -> dict:
    hit = ground(text, crop, connection, live=False)
    if not hit:
        return _refuse(language, "dose")
    record = hit.get("record") or {}
    quote = (record.get("answer") or "").strip()
    say = f"{quote} {CONFIRM[language]}".strip()
    return _pack(
        language,
        "dose",
        say,
        facts=[
            {
                "key": "kcc_answer",
                "value": quote,
                "unit": None,
                "as_of": record.get("created_on") or None,
            }
        ],
        citation=hit.get("citation"),
    )


def _rain(language: str, weather: dict, text: str, place: str | None, crop: str | None) -> dict:
    district = _district(text, place, crop)
    if district is None:
        return _ask(language, "place")
    if district not in PLACES:
        return _refuse(language, "out_of_domain")
    outlook = rain_outlook(weather, district)
    if not outlook.get("known"):
        return _refuse(language, "rain_missing", outlook.get("citation"))
    say = RAIN[language].format(district=district, mm=outlook["next_2d_mm"])
    if outlook.get("heavy"):
        say += RAIN_HEAVY[language]
    return _pack(
        language,
        "rain",
        say,
        facts=[
            {
                "key": "rain_2d",
                "value": outlook["next_2d_mm"],
                "unit": "mm",
                "as_of": (outlook.get("dates") or [None])[0],
            }
        ],
        citation=outlook.get("citation"),
    )


def _harvest(cards, language, text, crop, place, phone, sown_on, connection) -> dict:
    if crop is None:
        return _ask(language, "crop")
    if _place_conflicts(crop, text, place):
        return _refuse(language, "missing_series")
    sown = (sown_on or "").strip()[:10]
    if not sown and connection is not None and phone:
        row = _sowing(connection, phone, crop)
        sown = (row or {}).get("sow_date") or ""
    if not sown:
        return _ask(language, "sowing")
    try:
        started = date.fromisoformat(sown)
    except ValueError:
        return _ask(language, "sowing")
    window = CALL_WINDOW_DAYS[crop]
    opening = started + timedelta(days=window[0])
    closing = started + timedelta(days=window[1])
    line = HARVEST[language].format(
        crop=crop_name(crop, language),
        sown=started.isoformat(),
        start=opening.isoformat(),
        end=closing.isoformat(),
    )
    card = (cards.get(crop) or {}).get(language) or {}
    say = f"{line} {card['say']}".strip() if card.get("available") else line
    facts = list(card.get("facts") or [])
    facts.append({"key": "sown_on", "value": started.isoformat(), "unit": None, "as_of": started.isoformat()})
    facts.append({"key": "harvest_window_start", "value": opening.isoformat(), "unit": None, "as_of": None})
    facts.append({"key": "harvest_window_end", "value": closing.isoformat(), "unit": None, "as_of": None})
    return _pack(language, "harvest", say, facts=facts, citation=card.get("citation"))


def _sowing(connection, phone: str, crop: str) -> dict | None:
    number = normalize_phone(phone)
    if not number:
        return None
    row = connection.execute(
        "SELECT * FROM sowings WHERE phone = ? AND crop = ? ORDER BY sow_date DESC, id DESC LIMIT 1",
        (number, crop),
    ).fetchone()
    return dict(row) if row else None


def _district(text: str, place: str | None, crop: str | None) -> str | None:
    blob = f"{place or ''} {text}".lower()
    for name in PLACES:
        if name.lower() in blob:
            return name
    if crop:
        return CROPS[crop]["district"]
    return None


def _place_conflicts(crop: str, text: str, place: str | None) -> bool:
    blob = f"{place or ''} {text}".lower()
    if _mentions(blob, crop):
        return False
    if crop == "tomato" and any(token in blob for token in ("bengaluru", "bangalore", "binny")):
        others = [other for other in CROPS if other not in {crop, "banana"} and _mentions(blob, other)]
        extras = [name for name in OTHER_PLACES if name in blob]
        return bool(others or extras)
    others = [other for other in CROPS if other != crop and _mentions(blob, other)]
    extras = [name for name in OTHER_PLACES if name in blob]
    return bool(others or extras)


def _mentions(blob: str, crop: str) -> bool:
    row = CROPS[crop]
    tokens = [row["district"].lower(), *[item.lower() for item in row["keywords"]]]
    return any(token and token in blob for token in tokens)


def _has(text: str, word: str) -> bool:
    if word.isascii():
        return re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text, re.IGNORECASE) is not None
    return word in text


def _any(text: str, words: tuple[str, ...]) -> bool:
    return any(_has(text, word) for word in words)


def _match_crop(text: str) -> str | None:
    for name, crop in _NAMES:
        if _has(text, name):
            return crop
    return None


def _unknown_crop(text: str) -> bool:
    return _any(text, UNKNOWN_CROPS)


def _looks_farming(text: str) -> bool:
    hints = ("price", "rate", "mandi", "crop", "bhav", "भाव", "मंडी", "फसल", "ಬೆಲೆ", "ಮಂಡಿ", "ಬೆಳೆ")
    return _any(text, hints)


def _language(given: str | None, text: str) -> str:
    raw = (given or "").strip().lower().replace("_", "-")
    if raw.startswith("kn"):
        return "kn"
    if raw.startswith("hi"):
        return "hi"
    if raw.startswith("en"):
        return "en"
    for char in text:
        if "\u0c80" <= char <= "\u0cff":
            return "kn"
        if "\u0900" <= char <= "\u097f":
            return "hi"
    return "en"


def _pack(language, intent, say, ask=None, refused=False, facts=None, citation=None) -> dict:
    return {
        "language": language,
        "intent": intent,
        "say": say,
        "ask": ask,
        "refused": refused,
        "facts": facts or [],
        "citation": citation,
    }


def _ask(language: str, kind: str) -> dict:
    question = ASK[kind][language]
    return _pack(language, "clarify", question, ask=question)


def _refuse(language: str, kind: str, citation: str | None = None) -> dict:
    message = REFUSAL[kind][language]
    intent = "dose" if kind == "dose" else "out_of_domain"
    return _pack(language, intent, message, refused=True, citation=citation)
