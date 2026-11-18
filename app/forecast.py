"""Mandi advice for ten crops, and the scored tomato forecast."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.crops import NEARBY_MARGIN_RS, PERISHABLE, advice_phrase, transport_cost_rs

SOURCE = (
    "Agmarknet date-wise commodity report for Karnataka, "
    "api.agmarknet.gov.in. Prices are Rs per quintal. Arrivals are metric tonnes."
)
HOLDOUT_DAYS = 90
SELL_BAND = 0.03
SEASONAL_SELL = 1.05


def _series(frame: pd.DataFrame, crop: str, role: str) -> pd.DataFrame:
    part = frame[(frame["crop"] == crop) & (frame["role"] == role)].copy()
    if part.empty:
        return part
    part["date"] = pd.to_datetime(part["date"])
    return part.sort_values("date").reset_index(drop=True)


def _nearest(series: pd.DataFrame, target: pd.Timestamp, tolerance_days: int = 3):
    if series.empty:
        return None
    deltas = (series["date"] - target).abs()
    position = int(deltas.idxmin())
    if deltas.iloc[position] > pd.Timedelta(days=tolerance_days):
        return None
    return series.iloc[position]


def _window_sum(series: pd.DataFrame, end: pd.Timestamp, days: int = 7) -> float | None:
    if series.empty:
        return None
    start = end - pd.Timedelta(days=days)
    window = series[(series["date"] > start) & (series["date"] <= end)]
    if window.empty:
        return None
    return float(window["arrival_tonnes"].fillna(0).sum())


def _percentile(values: pd.Series, quantile: float) -> float | None:
    clean = values.dropna()
    if clean.empty:
        return None
    return float(np.quantile(clean.to_numpy(), quantile))


def wape(actual, predicted) -> float | None:
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    denom = np.abs(actual).sum()
    if denom == 0 or len(actual) == 0:
        return None
    return float(np.abs(actual - predicted).sum() / denom)


def _feature_row(series: pd.DataFrame, origin: pd.Timestamp):
    today = _nearest(series, origin, 0)
    if today is None:
        today = _nearest(series, origin, 1)
    if today is None or pd.Timestamp(today["date"]) != origin:
        # Origins are observed dates, so an exact row is required.
        exact = series[series["date"] == origin]
        if exact.empty:
            return None
        today = exact.iloc[0]
    lag7 = _nearest(series, origin - pd.Timedelta(days=7), 3)
    lag30 = _nearest(series, origin - pd.Timedelta(days=30), 4)
    seasonal = _nearest(series, origin + pd.Timedelta(days=7) - pd.Timedelta(days=365), 4)
    target = _nearest(series, origin + pd.Timedelta(days=7), 2)
    if lag7 is None or lag30 is None or seasonal is None:
        return None
    arrivals = _window_sum(series, origin, 7)
    if arrivals is None:
        arrivals = 0.0
    features = {
        "date": origin,
        "modal_t": float(today["modal_price"]),
        "lag7": float(lag7["modal_price"]),
        "lag30": float(lag30["modal_price"]),
        "seasonal": float(seasonal["modal_price"]),
        "arrivals": float(arrivals),
        "delta7": float(today["modal_price"]) - float(lag7["modal_price"]),
    }
    if target is not None:
        features["y"] = float(target["modal_price"])
        features["y_date"] = pd.Timestamp(target["date"])
    return features


FEATURE_COLUMNS = ["modal_t", "lag7", "lag30", "seasonal", "arrivals", "delta7"]


def _fit_ridge(train: pd.DataFrame, alpha: float = 1.0) -> dict:
    matrix = train[FEATURE_COLUMNS].to_numpy(dtype=float)
    target = train["y"].to_numpy(dtype=float)
    mean = matrix.mean(axis=0)
    scale = matrix.std(axis=0)
    scale[scale == 0] = 1.0
    scaled = (matrix - mean) / scale
    design = np.column_stack([np.ones(len(scaled)), scaled])
    penalty = np.eye(design.shape[1])
    penalty[0, 0] = 0.0
    gram = design.T @ design + alpha * penalty
    coef = np.linalg.lstsq(gram, design.T @ target, rcond=None)[0]
    return {"coef": coef, "mean": mean, "scale": scale, "alpha": alpha}


def _predict(model: dict, frame: pd.DataFrame) -> np.ndarray:
    matrix = frame[FEATURE_COLUMNS].to_numpy(dtype=float)
    scaled = (matrix - model["mean"]) / model["scale"]
    design = np.column_stack([np.ones(len(scaled)), scaled])
    return design @ model["coef"]


def fit_tomato(frame: pd.DataFrame) -> dict:
    series = _series(frame, "tomato", "primary")
    result = {
        "ready": False,
        "reason": "Tomato series is missing.",
        "model": None,
        "holdout": None,
        "metrics": None,
        "examples": [],
        "live": None,
    }
    if len(series) < 40:
        result["reason"] = "Not enough Kolar tomato days to fit a 7-day model."
        return result
    rows = []
    for origin in series["date"]:
        features = _feature_row(series, pd.Timestamp(origin))
        if features and "y" in features:
            rows.append(features)
    observed = pd.DataFrame(rows)
    if len(observed) < 40:
        result["reason"] = "Not enough complete tomato rows for lags and a same-week-last-year price."
        return result
    last_origin = observed["date"].max()
    cutoff = last_origin - pd.Timedelta(days=HOLDOUT_DAYS)
    train = observed[observed["date"] < cutoff]
    holdout = observed[observed["date"] >= cutoff].copy()
    if len(train) < 30 or len(holdout) < 8:
        result["reason"] = "The holdout split does not have enough tomato rows."
        return result
    model = _fit_ridge(train)
    holdout["model"] = _predict(model, holdout)
    holdout["persistence"] = holdout["modal_t"]
    holdout["seasonal_baseline"] = holdout["seasonal"]
    metrics = {
        "holdout_start": holdout["date"].min().date().isoformat(),
        "holdout_end": holdout["date"].max().date().isoformat(),
        "n": int(len(holdout)),
        "train_n": int(len(train)),
        "wape_model": wape(holdout["y"], holdout["model"]),
        "wape_persistence": wape(holdout["y"], holdout["persistence"]),
        "wape_seasonal": wape(holdout["y"], holdout["seasonal_baseline"]),
    }
    metrics["beats_both"] = (
        metrics["wape_model"] is not None
        and metrics["wape_persistence"] is not None
        and metrics["wape_seasonal"] is not None
        and metrics["wape_model"] < metrics["wape_persistence"]
        and metrics["wape_model"] < metrics["wape_seasonal"]
    )
    live_features = _feature_row(series, pd.Timestamp(series["date"].iloc[-1]))
    live = None
    if live_features:
        live_frame = pd.DataFrame([live_features])
        live = {
            "as_of": pd.Timestamp(live_features["date"]).date().isoformat(),
            "modal": live_features["modal_t"],
            "forecast_7d": float(_predict(model, live_frame)[0]),
            "forecast_30d_seasonal": None,
        }
        seasonal_30 = _nearest(series, pd.Timestamp(live_features["date"]) - pd.Timedelta(days=365) + pd.Timedelta(days=30), 5)
        if seasonal_30 is not None:
            live["forecast_30d_seasonal"] = float(seasonal_30["modal_price"])
    result.update(
        {
            "ready": True,
            "reason": "Ridge regression on lag prices, recent arrivals, and the same week last year.",
            "model": model,
            "holdout": holdout,
            "metrics": metrics,
            "examples": _examples(holdout, frame),
            "live": live,
        }
    )
    return result


def _advice_from_forecast(today: float, forecast: float) -> tuple[str, str]:
    if forecast < today * (1 - SELL_BAND):
        return "sell_now", "The 7-day forecast is at least 3 percent under today's modal."
    if forecast > today * (1 + SELL_BAND):
        return "hold", "The 7-day forecast is at least 3 percent over today's modal."
    return "hold", "The 7-day forecast is within 3 percent of today's modal."


def _call_on_row(row, nearby: pd.DataFrame) -> dict:
    action, because = _advice_from_forecast(float(row["modal_t"]), float(row["model"]))
    nearby_note = None
    if not nearby.empty:
        match = _nearest(nearby, pd.Timestamp(row["date"]), 2)
        if match is not None:
            net = float(match["modal_price"]) - transport_cost_rs()
            if net >= float(row["modal_t"]) + NEARBY_MARGIN_RS:
                action = "sell_nearby"
                because = (
                    f"Bengaluru modal Rs {match['modal_price']:.0f} covered transport "
                    f"of Rs {transport_cost_rs()} per quintal with Rs {NEARBY_MARGIN_RS} left over."
                )
                nearby_note = float(match["modal_price"])
    move = float(row["y"]) - float(row["modal_t"])
    if action in {"sell_now", "sell_nearby"}:
        cost = max(0.0, move)
        if cost:
            cost_note = "Rupees per quintal left on the table because the price rose after a sell call."
        else:
            cost_note = "The price did not rise after the sell call, so nothing was left on the table."
    else:
        cost = max(0.0, -move)
        if cost:
            cost_note = "Rupees per quintal given up because the price fell after a hold call."
        else:
            cost_note = "The price did not fall after the hold call."
    return {
        "date": pd.Timestamp(row["date"]).date().isoformat(),
        "modal": round(float(row["modal_t"]), 1),
        "forecast_7d": round(float(row["model"]), 1),
        "price_7d_later": round(float(row["y"]), 1),
        "action": action,
        "because": because,
        "nearby_modal": None if nearby_note is None else round(nearby_note, 1),
        "cost_rs_per_qtl": round(cost, 1),
        "cost_note": cost_note,
    }


def _examples(holdout: pd.DataFrame, frame: pd.DataFrame) -> list[dict]:
    if holdout.empty:
        return []
    nearby = _series(frame, "tomato", "nearby")
    scored = []
    for position in holdout.index:
        item = _call_on_row(holdout.loc[position], nearby)
        item["_position"] = position
        item["_modal"] = float(holdout.loc[position, "modal_t"])
        scored.append(item)
    spike = max(scored, key=lambda item: item["_modal"])
    others = [item for item in scored if item["_position"] != spike["_position"]]
    costly = max(others or scored, key=lambda item: item["cost_rs_per_qtl"])
    examples = []
    for label, item in (("spike", spike), ("costliest_call", costly)):
        public = {key: value for key, value in item.items() if not key.startswith("_")}
        public["label"] = label
        examples.append(public)
    return examples


def name_storage(crop: str, action: str, reason: str) -> tuple[str, str]:
    """A soft price is not a single 'hold'. Perishables stay on the vine. The rest go to a godown."""
    if action != "hold":
        return action, reason
    if crop in PERISHABLE:
        return (
            "delay_harvest",
            reason
            + " Delay harvest and leave the fruit on the vine for 3 to 4 days."
            + " Picked tomato and banana last 3 to 5 days without cold storage, so a harvested load is a sell, not a store.",
        )
    return "store", reason + " Store in a dry godown."


def apply_harvest_state(snap: dict, harvested: bool) -> dict:
    """Picked tomato or banana cannot wait. The vine advice is only before harvest."""
    if not snap or not harvested or snap.get("action") != "delay_harvest":
        return snap
    updated = dict(snap)
    updated["action"] = "sell_now"
    updated["action_label"] = advice_phrase("sell_now")
    updated["action_reason"] = (
        "This load is already harvested. Tomato and banana last 3 to 5 days without cold storage. "
        "Sell it. Do not hold picked fruit."
    )
    return updated


def grade_line(modal: float | None, min_price: float | None, max_price: float | None) -> str:
    if modal is None:
        return ""
    text = f"Most sales were at Rs {modal:.0f} per quintal"
    if min_price is not None and max_price is not None:
        text += f", ranging from Rs {min_price:.0f} to Rs {max_price:.0f} depending on grade"
    return text + "."


def seasonal_action(modal: float | None, seasonal: float | None) -> tuple[str, str]:
    if modal is None or seasonal is None or seasonal <= 0:
        return "unknown", "Same week last year is missing, so there is no sell or hold call."
    if modal >= seasonal * SEASONAL_SELL:
        return "sell_now", "Today's modal is at least 5 percent above the same week last year."
    return "hold", "Today's modal is not 5 percent above the same week last year."


def snapshot(frame: pd.DataFrame, crop: str, tomato_model: dict | None = None, gate: dict | None = None) -> dict:
    series = _series(frame, crop, "primary")
    empty = {
        "crop": crop,
        "available": False,
        "mandi": None,
        "district": None,
        "as_of": None,
        "modal": None,
        "min_price": None,
        "max_price": None,
        "arrival_7d_tonnes": None,
        "seasonal_modal": None,
        "seasonal_arrival_7d_tonnes": None,
        "shortage": None,
        "action": "unknown",
        "action_label": advice_phrase("unknown"),
        "action_reason": "No mandi series is loaded for this crop.",
        "grade_line": "",
        "price_source": None,
        "gate_entered_at": None,
        "forecast_7d": None,
        "forecast_30d_seasonal": None,
        "nearby": None,
        "citation": SOURCE,
    }
    if series.empty:
        return empty
    latest = series.iloc[-1]
    as_of = pd.Timestamp(latest["date"])
    modal = float(latest["modal_price"])
    min_price = None if pd.isna(latest["min_price"]) else float(latest["min_price"])
    max_price = None if pd.isna(latest["max_price"]) else float(latest["max_price"])
    price_source = "agmarknet"
    gate_entered_at = None
    if gate and gate.get("modal") is not None:
        modal = float(gate["modal"])
        price_source = "fpo_gate"
        gate_entered_at = gate.get("entered_at")
        # A morning range belongs to this gate. Yesterday's Agmarknet spread is a different day.
        if gate.get("min_price") is not None and gate.get("max_price") is not None:
            min_price = float(gate["min_price"])
            max_price = float(gate["max_price"])
        else:
            min_price = None
            max_price = None
    arrival_7d = _window_sum(series, as_of, 7)
    seasonal_row = _nearest(series, as_of - pd.Timedelta(days=365), 7)
    seasonal_modal = None if seasonal_row is None else float(seasonal_row["modal_price"])
    seasonal_arrival = None
    if seasonal_row is not None:
        seasonal_arrival = _window_sum(series, pd.Timestamp(seasonal_row["date"]), 7)
    recent = series[series["date"] >= as_of - pd.Timedelta(days=90)]
    p80 = _percentile(recent["modal_price"], 0.8)
    shortage = None
    if arrival_7d is not None and seasonal_arrival not in (None, 0) and p80 is not None:
        shortage = bool(arrival_7d < 0.6 * seasonal_arrival and modal >= p80)
    action, reason = seasonal_action(modal, seasonal_modal)
    forecast_7d = None
    forecast_30 = None
    if crop == "tomato" and tomato_model and tomato_model.get("ready") and tomato_model.get("live"):
        live = tomato_model["live"]
        forecast_7d = float(live["forecast_7d"])
        forecast_30 = live.get("forecast_30d_seasonal")
        action, reason = _advice_from_forecast(modal, forecast_7d)
    elif crop == "tomato":
        seasonal_30 = _nearest(series, as_of - pd.Timedelta(days=365) + pd.Timedelta(days=30), 5)
        if seasonal_30 is not None:
            forecast_30 = float(seasonal_30["modal_price"])
    nearby_payload = None
    if crop == "tomato":
        nearby = _series(frame, "tomato", "nearby")
        match = _nearest(nearby, as_of, 3)
        if match is not None:
            cost = transport_cost_rs()
            net = float(match["modal_price"]) - cost
            worth_it = net >= modal + NEARBY_MARGIN_RS
            nearby_payload = {
                "mandi": str(match["market"]),
                "as_of": pd.Timestamp(match["date"]).date().isoformat(),
                "modal": float(match["modal_price"]),
                "distance_km": 70,
                "transport_rs_per_qtl_per_km": 2,
                "transport_rs_per_qtl": cost,
                "worth_the_trip": worth_it,
            }
            if worth_it:
                action = "sell_nearby"
                reason = (
                    f"{match['market']} modal is Rs {match['modal_price']:.0f}. "
                    f"After Rs {cost} per quintal for about 70 km, Rs {NEARBY_MARGIN_RS} still remains. "
                    "The FPO can pool the load and book that trip. "
                    "A farmer who has taken input credit from a local commission agent should not divert alone."
                )
    action, reason = name_storage(crop, action, reason)
    citation = f"{SOURCE} Market: {latest['market']}. Latest reported day: {as_of.date().isoformat()}."
    if price_source == "fpo_gate":
        citation += (
            f" The modal used for advice is the FPO morning gate price entered at {gate_entered_at}. "
            "Agmarknet prints often lag 24 to 48 hours and can miss a weekend."
        )
    return {
        "crop": crop,
        "available": True,
        "mandi": str(latest["market"]),
        "district": str(latest["district"]),
        "as_of": as_of.date().isoformat(),
        "modal": round(modal, 1),
        "min_price": None if min_price is None else round(min_price, 1),
        "max_price": None if max_price is None else round(max_price, 1),
        "arrival_7d_tonnes": None if arrival_7d is None else round(arrival_7d, 2),
        "seasonal_modal": None if seasonal_modal is None else round(seasonal_modal, 1),
        "seasonal_arrival_7d_tonnes": None if seasonal_arrival is None else round(seasonal_arrival, 2),
        "shortage": shortage,
        "action": action,
        "action_label": advice_phrase(action),
        "action_reason": reason,
        "grade_line": grade_line(modal, min_price, max_price),
        "price_source": price_source,
        "gate_entered_at": gate_entered_at,
        "forecast_7d": None if forecast_7d is None else round(forecast_7d, 1),
        "forecast_30d_seasonal": None if forecast_30 is None else round(float(forecast_30), 1),
        "nearby": nearby_payload,
        "citation": citation,
        "points": _points(series),
    }


def _points(series: pd.DataFrame) -> list[dict]:
    tail = series.tail(120)
    points = []
    for row in tail.itertuples(index=False):
        points.append(
            {
                "date": pd.Timestamp(row.date).date().isoformat(),
                "modal": None if pd.isna(row.modal_price) else round(float(row.modal_price), 1),
                "min_price": None if pd.isna(row.min_price) else round(float(row.min_price), 1),
                "max_price": None if pd.isna(row.max_price) else round(float(row.max_price), 1),
                "arrival_tonnes": None if pd.isna(row.arrival_tonnes) else round(float(row.arrival_tonnes), 2),
            }
        )
    return points


def round_metric(value):
    if value is None:
        return None
    return round(float(value) * 100, 2)
