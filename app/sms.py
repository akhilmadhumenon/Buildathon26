"""SMS bodies filled only with numbers that came from the mandi snapshot."""

from __future__ import annotations

from app.crops import crop_name

ACTIONS = {
    "en": {
        "sell_now": "Advice: sell now.",
        "delay_harvest": "Advice: delay harvest. Leave it on the vine for 3 to 4 days. Do not store picked fruit.",
        "store": "Advice: store in a dry godown.",
        "sell_nearby": "Advice: FPO pool to {nearby}. Transport Rs {cost}/qtl. Do not divert alone if a local agent advanced inputs.",
        "unknown": "No sell or store advice. The comparison series is missing.",
    },
    "hi": {
        "sell_now": "सलाह: अभी बेचें.",
        "delay_harvest": "सलाह: तुड़ाई 3 से 4 दिन रोकें. फल बेल पर रहने दें. तोड़ी हुई फसल न रखें.",
        "store": "सलाह: सूखे गोदाम में रखें.",
        "sell_nearby": "सलाह: एफपीओ {nearby} भेजे. ढुलाई ₹{cost} प्रति क्विंटल. स्थानीय एजेंट का उधार हो तो अकेले मंडी न बदलें.",
        "unknown": "बेचने या रखने की सलाह नहीं. तुलना के आँकड़े नहीं हैं.",
    },
    "kn": {
        "sell_now": "ಸಲಹೆ: ಈಗ ಮಾರಾಟ ಮಾಡಿ.",
        "delay_harvest": "ಸಲಹೆ: ಕೊಯ್ಲು 3 ರಿಂದ 4 ದಿನ ತಡೆಹಿಡಿಯಿರಿ. ಹಣ್ಣನ್ನು ಬಳ್ಳಿಯ ಮೇಲೆ ಬಿಡಿ. ಕೊಯ್ದ ಫಸಲನ್ನು ಇಡಬೇಡಿ.",
        "store": "ಸಲಹೆ: ಒಣ ಗೋದಾಮಿನಲ್ಲಿ ಇಡಿ.",
        "sell_nearby": "ಸಲಹೆ: ಎಫ್‌ಪಿಒ {nearby} ಗೆ ಕಳುಹಿಸಲಿ. ಸಾಗಣೆ ₹{cost} ಪ್ರತಿ ಕ್ವಿಂಟಾಲ್. ಸ್ಥಳೀಯ ಏಜೆಂಟ್ ಸಾಲ ಇದ್ದರೆ ಏಕಾಂಗಿಯಾಗಿ ಮಂಡಿ ಬದಲಿಸಬೇಡಿ.",
        "unknown": "ಮಾರಾಟ ಸಲಹೆ ಇಲ್ಲ. ಹೋಲಿಕೆ ಅಂಕಿ ಇಲ್ಲ.",
    },
}

GRADE = {
    "en": "Most sales were at Rs {modal}/qtl, ranging from Rs {low} to Rs {high} by grade.",
    "hi": "ज्यादातर माल ₹{modal} प्रति क्विंटल बिका, ग्रेड के हिसाब से ₹{low} से ₹{high} तक.",
    "kn": "ಹೆಚ್ಚಿನ ಮಾರಾಟ ₹{modal} ಪ್ರತಿ ಕ್ವಿಂಟಾಲ್. ದರ್ಜೆಗೆ ತಕ್ಕಂತೆ ₹{low} ರಿಂದ ₹{high}.",
}

BODIES = {
    "en": "RaithaLine: {crop} at {mandi} on {date}. {grade} {action} Source: {source}.",
    "hi": "राइथलाइन: {mandi} में {crop}, {date}. {grade} {action} स्रोत: {source}.",
    "kn": "ರೈತಲೈನ್: {mandi} ನಲ್ಲಿ {crop}, {date}. {grade} {action} ಮೂಲ: {source}.",
}


def render_sms(snapshot: dict, language: str) -> str:
    language = language if language in BODIES else "en"
    action = snapshot.get("action") or "unknown"
    nearby = (snapshot.get("nearby") or {}).get("mandi") or "the other mandi"
    cost = (snapshot.get("nearby") or {}).get("transport_rs_per_qtl") or ""
    action_text = ACTIONS[language].get(action, ACTIONS[language]["unknown"]).format(nearby=nearby, cost=cost)
    modal = snapshot.get("modal")
    modal_text = "—" if modal is None else f"{modal:.0f}"
    low = snapshot.get("min_price")
    high = snapshot.get("max_price")
    if low is None or high is None:
        grade = {
            "en": f"Most sales were at Rs {modal_text}/qtl.",
            "hi": f"ज्यादातर माल ₹{modal_text} प्रति क्विंटल बिका.",
            "kn": f"ಹೆಚ್ಚಿನ ಮಾರಾಟ ₹{modal_text} ಪ್ರತಿ ಕ್ವಿಂಟಾಲ್.",
        }[language]
    else:
        grade = GRADE[language].format(modal=modal_text, low=f"{low:.0f}", high=f"{high:.0f}")
    crop = snapshot.get("crop")
    try:
        crop_label = crop_name(crop, language) if crop else "—"
    except KeyError:
        crop_label = crop or "—"
    source = "FPO gate" if snapshot.get("price_source") == "fpo_gate" else "Agmarknet"
    return BODIES[language].format(
        crop=crop_label,
        mandi=snapshot.get("mandi") or "—",
        date=snapshot.get("as_of") or "—",
        grade=grade,
        action=action_text,
        source=source,
    )
