"""Submission notes generated from the loaded series."""

from __future__ import annotations

import json
from pathlib import Path

from app.agents import weekly_plan
from app.climate import DELTA_NOTE, suitability
from app.forecast import round_metric

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"


def _pct(value) -> str:
    rounded = round_metric(value)
    return "n/a" if rounded is None else f"{rounded:.2f}%"


def write_price_report(tomato_model: dict, markets: dict) -> None:
    metrics = tomato_model.get("metrics") or {}
    lines = [
        "# Tomato at Kolar: 7-day modal forecast",
        "",
        f"Primary market: {markets.get('tomato', 'Kolar APMC')}.",
        "Prices are Agmarknet modal prices in rupees per quintal.",
        "The model is a ridge regression. Features on the day the forecast is made: that day's modal, the modal about 7 and 30 days earlier, arrivals over those 7 days, the change over 7 days, and the modal from the same point in the previous year.",
        "Holdout: the most recent 90 days of forecast origins whose 7-day outcome is already known.",
        "For a 7-day horizon, \"tomorrow equals today\" means the forecast equals the modal on the day the forecast is made.",
        "\"Same week last year\" means the modal about 365 days before the target day.",
        "",
    ]
    if not tomato_model.get("ready"):
        lines.append(f"The model was not fit. {tomato_model.get('reason')}")
    else:
        beats = metrics.get("beats_both")
        lines.extend(
            [
                f"Holdout origins: {metrics['holdout_start']} to {metrics['holdout_end']} ({metrics['n']} days). Training rows: {metrics['train_n']}.",
                "",
                f"- Model weighted absolute percentage error: {_pct(metrics['wape_model'])}",
                f"- Tomorrow-equals-today weighted absolute percentage error: {_pct(metrics['wape_persistence'])}",
                f"- Same-week-last-year weighted absolute percentage error: {_pct(metrics['wape_seasonal'])}",
                "",
                (
                    "On this holdout the model is lower than both baselines."
                    if beats
                    else "On this holdout the model does not beat both baselines. The baselines stay in the table so the gap is visible."
                ),
                "",
                "The 30-day number shown in the product is the same season last year, shifted by 30 days. It is not a second trained model.",
                "",
                "## Two dated calls",
                "",
            ]
        )
        for example in tomato_model.get("examples") or []:
            lines.extend(
                [
                    f"### {example['label'].replace('_', ' ')} on {example['date']}",
                    "",
                    f"Modal that day: Rs {example['modal']:.0f}. Model 7-day forecast: Rs {example['forecast_7d']:.0f}. "
                    f"Modal about 7 days later: Rs {example['price_7d_later']:.0f}.",
                    f"Call: {example['action'].replace('_', ' ')}. {example['because']}",
                    f"Cost of being wrong: Rs {example['cost_rs_per_qtl']:.0f} per quintal. {example['cost_note']}",
                    "",
                ]
            )
    lines.extend(
        [
            "The other nine crops do not get this model. Their sell or store call is whether today's modal is at least 5 percent above the same week last year.",
            "On the live line a soft tomato or banana price is delay harvest, leave the fruit on the vine for 3 to 4 days. A harvested load of those two is a sell: they last 3 to 5 days without cold storage. A soft price for the other eight crops is a dry godown.",
            "The spoken quote is the modal plus that day's minimum and maximum, because grade moves the same mandi on the same morning. An FPO gate price can replace the lagged Agmarknet modal before calls go out.",
            "Shortage, for every crop, means 7-day arrivals under 60 percent of the same week last year and a modal in the top fifth of the last 90 days.",
            "",
        ]
    )
    (REPORTS / "price_baselines.md").write_text("\n".join(lines))


def write_climate_report(cache: dict) -> None:
    cards = suitability(cache)
    lines = [
        "# Climate screen for ten Karnataka crops",
        "",
        "Two districts: Kolar and Kalaburagi. Normals are the 2015–2024 mean temperature and mean annual rainfall from Open-Meteo.",
        DELTA_NOTE,
        "",
        "Score = 0.6 × temperature limb + 0.4 × rainfall limb. A limb is 100 inside the EcoCrop optimal band, 0 outside the absolute band, and linear between them.",
        "Limits are FAO EcoCrop species parameters shipped with the R package dismo (`ECOcrops`). Dry chilli uses Capsicum annuum. Banana uses the cultivated Musa hybrid. Paddy uses indica rice. These are species screens, not variety advice, and they were not checked against a soil grid or against historical sown area.",
        "",
    ]
    for district in ("Kolar", "Kalaburagi"):
        subset = [card for card in cards if card["district"] == district]
        if not subset:
            continue
        place = subset[0]
        lines.append(f"## {district}")
        lines.append("")
        lines.append(f"Normal mean temperature {place['tmean_c']}°C. Normal annual rainfall {place['precip_mm']} mm.")
        lines.append("")
        lines.append("| Crop | Today | Moderate 2041–2060 | High 2041–2060 | Change under high |")
        lines.append("| --- | --- | --- | --- | --- |")
        for card in sorted(subset, key=lambda item: item["scenarios"]["today"]["score"], reverse=True):
            lines.append(
                f"| {card['crop_en']} | {card['scenarios']['today']['score']} | "
                f"{card['scenarios']['ssp245_2041_2060']['score']} | "
                f"{card['scenarios']['ssp585_2041_2060']['score']} | {card['change_high']} |"
            )
        lines.append("")
        ranked = sorted(subset, key=lambda item: item["scenarios"]["today"]["score"], reverse=True)
        top = ", ".join(f"{card['crop_en']} ({card['scenarios']['today']['score']})" for card in ranked[:3])
        losers = [card for card in sorted(subset, key=lambda item: item["change_high"]) if card["change_high"] < 0][:3]
        loss = ", ".join(f"{card['crop_en']} ({card['change_high']})" for card in losers) or "no crop loses score"
        lines.append(f"Highest today: {top}.")
        lines.append(f"Largest drop under the high screen: {loss}.")
        lines.append("")
    lines.append("Paddy's rainfall band starts high, so a rainfed screen marks the interior districts down. Irrigated paddy is outside what this score can see.")
    lines.append("")
    lines.append("When a farmer marks the load in transit or completes the sale, the line speaks this screen for their crop against drought-hardy ragi and tur, before the next sowing. It is still a species screen, not a variety recommendation.")
    lines.append("")
    (REPORTS / "climate_cards.md").write_text("\n".join(lines))


def write_plan_and_refusals(frame, tomato_model, weather_cache, climate_cache) -> None:
    profile = {
        "crop": "tomato",
        "sowing_date": "2026-08-19",
        "district": "Kolar",
        "soil_type": "red loam",
    }
    plan = weekly_plan(frame, tomato_model, weather_cache, climate_cache, profile)
    (REPORTS / "agent_log.json").write_text(json.dumps({"profile": profile, "result": plan}, indent=2, default=str))
    cases = [
        ("Dose", {"crop": "tomato", "sowing_date": "2026-08-19", "district": "Kolar", "question": "What pesticide dose should I spray on tomato?"}),
        ("Outside farming", {"crop": "tomato", "sowing_date": "2026-08-19", "district": "Kolar", "question": "Who won the cricket world cup?"}),
        ("Crop not on the line", {"crop": "coffee", "sowing_date": "2026-06-01", "district": "Kodagu", "question": "Should I sell coffee?"}),
        ("Missing series", {"crop": "tomato", "sowing_date": "2026-08-19", "district": "Kolar", "question": None, "_force_missing": True}),
    ]
    lines = ["# Cases the line refuses", ""]
    for title, body in cases:
        if body.get("_force_missing"):
            empty = frame.iloc[0:0]
            result = weekly_plan(
                empty,
                tomato_model,
                weather_cache,
                climate_cache,
                {"crop": "tomato", "sowing_date": "2026-08-19", "district": "Kolar"},
            )
            lines.append(f"## {title}")
            lines.append("")
            lines.append("Same weekly-plan function, with an empty price table.")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(result, indent=2, default=str))
            lines.append("```")
            lines.append("")
            continue
        result = weekly_plan(frame, tomato_model, weather_cache, climate_cache, body)
        public = {key: value for key, value in result.items() if key != "snapshot"}
        lines.append(f"## {title}")
        lines.append("")
        lines.append(f"Request question: {body.get('question') or body.get('crop')}")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(public, indent=2, default=str))
        lines.append("```")
        lines.append("")
    lines.append("Krishi Vigyan Kendra is the place named when someone asks for a dose.")
    lines.append("")
    (REPORTS / "refusals.md").write_text("\n".join(lines))


def market_names(frame) -> dict:
    names = {}
    if frame is None or frame.empty:
        return names
    primary = frame[frame["role"] == "primary"]
    for crop, part in primary.groupby("crop"):
        names[crop] = str(part["market"].iloc[0])
    return names
