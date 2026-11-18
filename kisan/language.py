"""Detect English, Hindi, or Kannada and pick a matching Sarvam voice."""

from __future__ import annotations

import re

# Companion and conversation personas. Pace 1 and pitch 0 are Bulbul's conversational defaults.
VOICES = {
    "en": ("en-IN", "aparna_en_companion"),
    "hi": ("hi-IN", "shreya_hi_conversational"),
    "kn": ("kn-IN", "chaitra_kn_conversation"),
}
SPEECH_PACE = 1.0
SPEECH_PITCH = 0.0
# Just enough quiet for the last syllable to finish on the phone line.
SPEECH_TAIL = 0.2

_OPENERS = ("ಹೌದು", "जी हाँ", "जी हां", "Of course", "Certainly", "Yes")


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


def spoken_text(text: str, language: str) -> str:
    """Punctuation Bulbul can speak in one breath, without a shouted or chopped contour.

    A comma is a short breath, a full stop ends a thought, and a question mark
    lifts the voice. The whole reply stays one clip so the tone can connect.
    """
    cleaned = " ".join((text or "").replace("\n", " ").split())
    if not cleaned:
        return ""
    cleaned = re.sub(r"[\"“”«»]", "", cleaned)
    cleaned = re.sub(r"[()\[\]{}]", ", ", cleaned)
    cleaned = re.sub(r"[;:—–]", ", ", cleaned)
    cleaned = re.sub(r"(?:…|\.{3,})(?=\s|$)", ".", cleaned)
    cleaned = re.sub(r"(?:…|\.{3,})", ", ", cleaned)
    cleaned = re.sub(r"!+", ".", cleaned)
    cleaned = re.sub(r"\?+", "?", cleaned)
    cleaned = re.sub(r"(?:\.\s*)+\?", "?", cleaned)
    cleaned = re.sub(r"\?\.+", "?", cleaned)
    cleaned = re.sub(r"\.{2,}", ".", cleaned)
    cleaned = re.sub(r"\s+([,.।?])", r"\1", cleaned)
    cleaned = re.sub(r"([,.।?])\1+", r"\1", cleaned)
    cleaned = re.sub(r",\s*,+", ", ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,")
    if not cleaned:
        return ""
    if language in {"hi", "kn"}:
        cleaned = re.sub(r"\.(?=\s|$)", "।", cleaned)
    cleaned = _without_stock_opener(cleaned)
    if not cleaned:
        return ""
    if cleaned[-1] not in ".।?":
        cleaned += "।" if language in {"hi", "kn"} else "."
    return cleaned


def _without_stock_opener(text: str) -> str:
    """Drop a barked Yes or ಹೌದು. The rest of the sentence stays."""
    for opener in _OPENERS:
        if text.lower().startswith(opener.lower()):
            rest = text[len(opener):]
            if rest and rest[0].isalpha():
                continue
            rest = rest.lstrip(" ,!.:;-।")
            return rest or text
    return text


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
