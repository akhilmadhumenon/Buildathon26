"""Crop and place names, and the caller's details for this call.

Mandi prices are not stored here. They come from the RaithaLine dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass


CROP_ALIASES = {
    "ragi": "ragi",
    "finger millet": "ragi",
    "tomato": "tomato",
    "tomatoes": "tomato",
    "tamatar": "tomato",
    "tamater": "tomato",
    "tamaatar": "tomato",
    "maize": "maize",
    "corn": "maize",
    "paddy": "paddy",
    "rice": "paddy",
    "onion": "onion",
    "onions": "onion",
    "chilli": "chilli",
    "chilies": "chilli",
    "chilli pepper": "chilli",
    "chili": "chilli",
    "टमाटर": "tomato",
    "रागी": "ragi",
    "मक्का": "maize",
    "धान": "paddy",
    "प्याज": "onion",
    "मिर्च": "chilli",
    "ಟೊಮೇಟೊ": "tomato",
    "ಟೊಮೆಟೊ": "tomato",
    "ಟಮಾಟೊ": "tomato",
    "ಟೊಮಾಟೊ": "tomato",
    "ಟೊಮ್ಯಾಟೊ": "tomato",
    "ರಾಗಿ": "ragi",
    "ಮೆಕ್ಕೆಜೋಳ": "maize",
    "ಜೋಳ": "maize",
    "jola": "maize",
    "jowar": "maize",
    "ಭತ್ತ": "paddy",
    "ಈರುಳ್ಳಿ": "onion",
    "ಮೆಣಸಿನಕಾಯಿ": "chilli",
    "banana": "banana",
    "bananas": "banana",
    "mango": "mango",
    "mangoes": "mango",
    "grape": "grapes",
    "grapes": "grapes",
    "केला": "banana",
    "आम": "mango",
    "अंगूर": "grapes",
    "ಬಾಳೆಹಣ್ಣು": "banana",
    "ಮಾವಿನಹಣ್ಣು": "mango",
    "ದ್ರಾಕ್ಷಿ": "grapes",
    "potato": "potato",
    "potatoes": "potato",
    "आलू": "potato",
    "ಆಲೂಗಡ್ಡೆ": "potato",
    "tur": "tur",
    "arhar": "tur",
    "toor": "tur",
    "pigeon pea": "tur",
    "red gram": "tur",
    "अरहर": "tur",
    "तूर": "tur",
    "ತೊಗರಿ": "tur",
    "groundnut": "groundnut",
    "groundnuts": "groundnut",
    "peanut": "groundnut",
    "peanuts": "groundnut",
    "मूंगफली": "groundnut",
    "ಕಡಲೆಕಾಯಿ": "groundnut",
}

PLACE_ALIASES = {
    "mandya": "Mandya",
    "mysuru": "Mysuru",
    "mysore": "Mysuru",
    "kolar": "Kolar",
    "davangere": "Davangere",
    "davanagere": "Davangere",
    "hubballi": "Hubballi",
    "hubli": "Hubballi",
    "gangavathi": "Gangavathi",
    "gangavati": "Gangavathi",
    "bengaluru": "Bengaluru",
    "bangalore": "Bengaluru",
    "hassan": "Hassan",
    "tumakuru": "Tumakuru",
    "tumkur": "Tumakuru",
    "raichur": "Raichur",
    "ballari": "Ballari",
    "bellary": "Ballari",
    "shivamogga": "Shivamogga",
    "shimoga": "Shivamogga",
    "dharwad": "Dharwad",
    "धारवाड़": "Dharwad",
    "ಧಾರವಾಡ": "Dharwad",
    "chitradurga": "Chitradurga",
    "kalaburagi": "Kalaburagi",
    "gulbarga": "Kalaburagi",
    "kalburgi": "Kalaburagi",
    "कोलार": "Kolar",
    "मंड्या": "Mandya",
    "हुबली": "Hubballi",
    "बेंगलुरु": "Bengaluru",
    "ಕೋಲಾರ": "Kolar",
    "ಮಂಡ್ಯ": "Mandya",
    "ಹುಬ್ಬಳ್ಳಿ": "Hubballi",
    "ಬೆಂಗಳೂರು": "Bengaluru",
    "huskur": "Huskur",
    "huskuru": "Huskur",
    "huskhur": "Huskur",
    "ಹುಸ್ಕೂರು": "Huskur",
    "हुसकुर": "Huskur",
}

# District centres used to measure a caller's village against a reporting mandi.
DISTRICT_COORDS = {
    "mandya": (12.52, 76.90),
    "mysuru": (12.30, 76.64),
    "kolar": (13.14, 78.13),
    "davangere": (14.47, 75.92),
    "hubballi": (15.36, 75.12),
    "gangavathi": (15.43, 76.53),
    "bengaluru": (12.97, 77.59),
    "huskur": (12.843, 77.689),
    "hassan": (13.01, 76.10),
    "tumakuru": (13.34, 77.10),
    "raichur": (16.21, 77.36),
    "ballari": (15.14, 76.92),
    "shivamogga": (13.93, 75.57),
    "dharwad": (15.46, 75.01),
    "chitradurga": (14.23, 76.40),
    "kalaburagi": (17.33, 76.83),
}

# Days the harvested crop can wait, and the condition that limit applies to.
KEEPING = {
    "banana": (4, "once ripe; a little longer if still green"),
    "mango": (5, "once ripe"),
    "grapes": (4, "once picked"),
    "tomato": (5, "once ripe"),
    "chilli": (5, "fresh pods"),
    "onion": (30, "if dry"),
    "maize": (90, "if dry"),
    "paddy": (180, "after drying"),
    "ragi": (180, "after drying"),
    "potato": (60, "if cured and dry"),
    "tur": (180, "after drying"),
    "groundnut": (90, "if dry"),
}


@dataclass
class Caller:
    """Number and place for this call. Earlier calls are loaded from Postgres."""

    phone: str = ""
    place: str = ""
    memory: str = ""


def normalize_phone(raw: str | None) -> str:
    """Store Indian mobiles as +91 and ten digits so repeat calls match."""
    if not raw:
        return ""
    digits = "".join(char for char in raw if char.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10:
        return "+91" + digits
    cleaned = raw.strip()
    return cleaned


def canonical_place(value: str | None) -> str:
    key = " ".join((value or "").strip().lower().split())
    return PLACE_ALIASES.get(key, (value or "").strip())
