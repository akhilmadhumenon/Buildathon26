"""Snap a misheard crop, place, or mandi word back to the name we know."""

from __future__ import annotations

import re

from kisan.reference import CROP_ALIASES, PLACE_ALIASES

_EXTRA = {
    "mandi": "mandi",
    "market": "market",
    "quintal": "quintal",
    "harvest": "harvest",
    "rupee": "rupee",
    "rupees": "rupees",
}


def skeleton(value: str) -> str:
    """Consonant shape, so Koodlu and Kudlu, or Monday and mandi, compare equal."""
    text = (value or "").lower()
    text = text.replace("oo", "u").replace("ee", "i").replace("aa", "a")
    text = re.sub(r"[^a-z]", "", text)
    text = text.replace("ph", "f").replace("th", "t").replace("dh", "d").replace("w", "v")
    text = text.replace("h", "")
    text = re.sub(r"[aeiouy]", "", text)
    return re.sub(r"(.)\1+", r"\1", text)


def _shapes(source: dict[str, str], use_alias: bool = False) -> dict[str, str]:
    grouped: dict[str, set[str]] = {}
    for alias, name in source.items():
        if any(ord(char) > 127 for char in alias):
            continue
        shape = skeleton(alias)
        if len(shape) < 2:
            continue
        grouped.setdefault(shape, set()).add(alias if use_alias else name)
    return {shape: names.pop() for shape, names in grouped.items() if len(names) == 1}


_KNOWN = {key.lower() for key in (*CROP_ALIASES, *PLACE_ALIASES, *_EXTRA)}
_NAMES = _shapes({**CROP_ALIASES, **PLACE_ALIASES})
_WORDS = _shapes(_EXTRA, use_alias=True)


def correct_transcript(text: str) -> str:
    """Replace a misheard token when only one known name has the same sound."""
    def swap(match: re.Match[str]) -> str:
        word = match.group(0)
        if any(ord(char) > 127 for char in word):
            return word
        key = word.lower()
        if key in _KNOWN:
            return word
        replacement = _WORDS.get(skeleton(word)) or _NAMES.get(skeleton(word))
        if not replacement or replacement.lower() == key:
            return word
        if word[:1].isupper():
            return replacement[:1].upper() + replacement[1:]
        return replacement

    return re.sub(r"[A-Za-z]+", swap, text or "")
