"""The ten Karnataka crops this line will talk about."""

from __future__ import annotations

# Tomato and banana spoil in a few days once picked. A soft price means leave
# them on the vine, not a godown. The other eight keep in storage.
PERISHABLE = {"tomato", "banana"}

# Practical harvest window in days after sowing, used only to decide whether
# a farmer is close enough to harvest to receive a mandi call. EcoCrop cycle
# lengths are wider and stay on the climate card.
CALL_WINDOW_DAYS = {
    "tomato": (70, 120),
    "onion": (90, 150),
    "potato": (80, 120),
    "chilli": (110, 180),
    "banana": (240, 400),
    "ragi": (90, 140),
    "maize": (85, 130),
    "tur": (140, 200),
    "paddy": (100, 160),
    "groundnut": (90, 140),
}

# Preferred Agmarknet market names, lowercase. The loader uses the first
# preference that actually has a series, then a keyword fallback.
CROPS = {
    "tomato": {
        "en": "Tomato",
        "hi": "टमाटर",
        "kn": "ಟೊಮೇಟೊ",
        "district": "Kolar",
        "markets": ["kolar apmc"],
        "nearby_markets": ["binny mill (ff&v) bengaluru apmc", "binny mill"],
        "keywords": ["kolar"],
    },
    "onion": {
        "en": "Onion",
        "hi": "प्याज",
        "kn": "ಈರುಳ್ಳಿ",
        "district": "Dharwad",
        "markets": ["apmc hubballi", "hubballi", "hubli"],
        "nearby_markets": [],
        "keywords": ["hubballi", "hubli"],
    },
    "potato": {
        "en": "Potato",
        "hi": "आलू",
        "kn": "ಆಲೂಗಡ್ಡೆ",
        "district": "Hassan",
        "markets": ["hassan apmc"],
        "nearby_markets": [],
        "keywords": ["hassan apmc"],
    },
    "chilli": {
        "en": "Dry chilli",
        "hi": "सूखी मिर्च",
        "kn": "ಒಣ ಮೆಣಸಿನಕಾಯಿ",
        "district": "Dharwad",
        "markets": ["apmc hubballi", "byadagi apmc"],
        "nearby_markets": [],
        "keywords": ["hubballi", "byadag"],
    },
    "banana": {
        "en": "Banana",
        "hi": "केला",
        "kn": "ಬಾಳೆಹಣ್ಣು",
        "district": "Bengaluru",
        "markets": ["bengaluru apmc", "binny mill (ff&v) bengaluru apmc"],
        "nearby_markets": [],
        "keywords": ["bengaluru", "bangalore", "binny"],
    },
    "ragi": {
        "en": "Ragi",
        "hi": "रागी",
        "kn": "ರಾಗಿ",
        "district": "Mysuru",
        "markets": ["mysuru apmc", "davangere apmc"],
        "nearby_markets": [],
        "keywords": ["mysuru", "mysore"],
    },
    "maize": {
        "en": "Maize",
        "hi": "मक्का",
        "kn": "ಮೆಕ್ಕೆಜೋಳ",
        "district": "Davangere",
        "markets": ["davangere apmc", "davanagere apmc"],
        "nearby_markets": [],
        "keywords": ["davangere", "davanagere"],
    },
    "tur": {
        "en": "Tur",
        "hi": "तुअर",
        "kn": "ತೊಗರಿ",
        "district": "Kalaburagi",
        "markets": ["kalaburagi apmc", "gulbarga apmc"],
        "nearby_markets": [],
        "keywords": ["kalaburagi", "gulbarga"],
    },
    "paddy": {
        "en": "Paddy",
        "hi": "धान",
        "kn": "ಭತ್ತ",
        "district": "Raichur",
        "markets": ["raichur apmc"],
        "nearby_markets": [],
        "keywords": ["raichur"],
    },
    "groundnut": {
        "en": "Groundnut",
        "hi": "मूँगफली",
        "kn": "ಕಡಲೆಕಾಯಿ",
        "district": "Chitradurga",
        "markets": ["chitradurga apmc"],
        "nearby_markets": [],
        "keywords": ["chitradurga"],
    },
}

ALIASES = {
    "tomato": "tomato",
    "onion": "onion",
    "potato": "potato",
    "chilli": "chilli",
    "chili": "chilli",
    "dry chilli": "chilli",
    "dry chili": "chilli",
    "banana": "banana",
    "ragi": "ragi",
    "finger millet": "ragi",
    "maize": "maize",
    "corn": "maize",
    "tur": "tur",
    "arhar": "tur",
    "toor": "tur",
    "pigeon pea": "tur",
    "red gram": "tur",
    "paddy": "paddy",
    "rice": "paddy",
    "dhan": "paddy",
    "groundnut": "groundnut",
    "peanut": "groundnut",
}

# Kolar to the Bengaluru vegetable yard is about 70 km by road.
TRANSPORT_RS_PER_QTL_PER_KM = 2
TOMATO_NEARBY_KM = 70
# Recommend the other mandi only when the price gap covers transport
# and still leaves Rs 100 per quintal.
NEARBY_MARGIN_RS = 100

ADVICE_PHRASE = {
    "sell_now": "Sell now",
    "sell_nearby": "Sell nearby through the FPO",
    "delay_harvest": "Delay harvest. Leave it on the vine for 3 to 4 days",
    "store": "Store in a dry godown",
    "unknown": "No advice",
}


def advice_phrase(action: str) -> str:
    return ADVICE_PHRASE.get(action, action.replace("_", " "))

PLACES = {
    "Kolar": (13.136, 78.129),
    "Kalaburagi": (17.329, 76.834),
    "Dharwad": (15.459, 75.007),
    "Bengaluru": (12.972, 77.594),
    "Hassan": (13.007, 76.096),
    "Haveri": (14.795, 75.399),
    "Mandya": (12.522, 76.895),
    "Davangere": (14.464, 75.922),
    "Raichur": (16.207, 77.356),
    "Chitradurga": (14.226, 76.398),
}

CLIMATE_DISTRICTS = ("Kolar", "Kalaburagi")

# Uniform screening deltas versus the 2015–2024 normals. These sit in the
# IPCC AR6 South Asia mid-century range above the recent climate. They are
# not a downscaled WorldClim field.
CLIMATE_DELTAS = {
    "today": 0.0,
    "ssp245_2041_2060": 1.2,
    "ssp585_2041_2060": 1.9,
}


def canonical_crop(value: str | None) -> str | None:
    if not value:
        return None
    key = " ".join(value.strip().lower().replace("_", " ").split())
    if key in CROPS:
        return key
    return ALIASES.get(key)


def crop_name(crop: str, language: str) -> str:
    row = CROPS[crop]
    return row.get(language) or row["en"]


def transport_cost_rs() -> int:
    return int(TRANSPORT_RS_PER_QTL_PER_KM * TOMATO_NEARBY_KM)
