"""Detect English, Hindi, or Kannada and pick a matching Sarvam voice."""

from __future__ import annotations

import re

# Companion and conversation personas. A slightly brighter pitch keeps the phone voice cheerful.
VOICES = {
    "en": ("en-IN", "aparna_en_companion"),
    "hi": ("hi-IN", "shreya_hi_conversational"),
    "kn": ("kn-IN", "chaitra_kn_conversation"),
}
SPEECH_PACE = 1.0
SPEECH_PITCH = 0.0

_SENTENCE_BREAK = re.compile(r"(?<=[.!?।])\s+")


def detect_language(text: str, hint: str | None = None, current: str | None = None) -> str:
    """Return 'en', 'hi', or 'kn'.

    The script of the words wins, so Kannada or Hindi is never answered in English.
    A short follow-up written in Latin, such as a crop name, stays in the language
    already established on this call.
    """
    script = _script_language(text)
    if script in {"kn", "hi"}:
        return script
    if _roman_hindi(text):
        return "hi"

    hinted = _from_hint(hint)
    if hinted in {"kn", "hi"}:
        return hinted

    latin_words = re.findall(r"[A-Za-z]+", text or "")
    if current in {"kn", "hi"} and len(latin_words) < 6:
        return current
    if hinted == "en" or script == "en":
        return "en"
    return current or "en"


def _script_language(text: str) -> str | None:
    counts = {"kn": 0, "hi": 0, "en": 0}
    for char in text or "":
        code = ord(char)
        if 0x0C80 <= code <= 0x0CFF:
            counts["kn"] += 1
        elif 0x0900 <= code <= 0x097F:
            counts["hi"] += 1
        elif char.isascii() and char.isalpha():
            counts["en"] += 1
    best = max(counts, key=counts.get)
    if counts[best] == 0:
        return None
    return best


def voice_for(language: str) -> tuple[str, str]:
    return VOICES.get(language, VOICES["en"])


def speech_segments(text: str, language: str) -> list[str]:
    """Split a reply into short spoken lines so the voice can breathe."""
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return []
    if language in {"hi", "kn"}:
        cleaned = re.sub(r"\.(?=\s|$)", "।", cleaned)
    parts = [part.strip() for part in _SENTENCE_BREAK.split(cleaned) if part.strip()]
    return parts or [cleaned]


def _roman_hindi(text: str) -> bool:
    words = {word.lower() for word in re.findall(r"[A-Za-z]+", text or "")}
    markers = {
        "mujhe", "bechna", "bechne", "becho", "bech", "chahiye", "kitna", "kitne",
        "kahan", "batao", "bataiye", "bhav", "bhaav", "rupaye", "rupya",
    }
    return bool(words & markers)


def _from_hint(hint: str | None) -> str | None:
    if not hint:
        return None
    token = hint.strip().lower().replace("_", "-")
    if token.startswith("kn"):
        return "kn"
    if token.startswith("hi"):
        return "hi"
    if token.startswith("en"):
        return "en"
    return None
