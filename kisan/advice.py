"""Small helpers for deciding whether a sentence should be answered."""

from __future__ import annotations

import math
import re

from kisan.reference import CROP_ALIASES, PLACE_ALIASES


GOODBYE = {
    "en": "Alright, take care.",
    "hi": "ठीक है, अपना ध्यान रखिए।",
    "kn": "ಸರಿ, ಜೋಪಾನವಾಗಿರಿ.",
}


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


def _km_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1 = math.radians(lat1), math.radians(lon1)
    lat2, lon2 = math.radians(lat2), math.radians(lon2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    arc = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(arc))
