"""Inbound session, turn, and the SMS returned when a call ends."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from app.agents import refusal
from app.climate import next_sowing_brief
from app.crops import CROPS, PLACES, canonical_crop
from app.db import rows
from app.forecast import apply_harvest_state, snapshot
from app.kcc import ground
from app.sms import render_sms


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_phone(value: str | None) -> str:
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    return digits


def _farmer(connection, phone: str) -> dict | None:
    row = connection.execute("SELECT * FROM farmers WHERE phone = ?", (phone,)).fetchone()
    return dict(row) if row else None


def _sowing(connection, phone: str) -> dict | None:
    row = connection.execute(
        "SELECT * FROM sowings WHERE phone = ? ORDER BY sow_date DESC, id DESC LIMIT 1",
        (phone,),
    ).fetchone()
    return dict(row) if row else None


def _location(farmer: dict | None, body: dict) -> dict:
    lat = body.get("lat")
    lon = body.get("lon")
    if lat is not None and lon is not None:
        return {
            "district": (farmer or {}).get("district"),
            "village": (farmer or {}).get("village"),
            "lat": lat,
            "lon": lon,
            "source": "gateway_coordinates",
            "confidence": "high",
            "ask_village": False,
        }
    if farmer and farmer.get("district"):
        return {
            "district": farmer["district"],
            "village": farmer.get("village"),
            "lat": farmer.get("lat"),
            "lon": farmer.get("lon"),
            "source": "saved_profile",
            "confidence": "high",
            "ask_village": False,
        }
    return {
        "district": None,
        "village": None,
        "lat": None,
        "lon": None,
        "source": "unknown",
        "confidence": "low",
        "ask_village": True,
    }


def _gate(gates: dict | None, crop: str | None) -> dict | None:
    if not gates or not crop:
        return None
    return gates.get(crop)


def _priced(frame, tomato_model, crop: str, gates, sowing: dict | None) -> dict:
    snap = snapshot(frame, crop, tomato_model, _gate(gates, crop))
    harvested = bool(sowing and sowing.get("crop") == crop and sowing.get("harvested"))
    return apply_harvest_state(snap, harvested)


def _facts(snap: dict) -> list[dict]:
    if not snap or not snap.get("available"):
        return []
    label = snap.get("action_label") or snap["action"].replace("_", " ")
    text = f"{snap.get('grade_line') or ''} Advice: {label}. {snap['action_reason']}"
    if snap.get("price_source") == "fpo_gate":
        text += (
            f" The modal is the FPO morning gate price entered at {snap.get('gate_entered_at')}. "
            "Do not quote the lagged Agmarknet modal."
        )
    return [{"text": " ".join(text.split()), "citation": snap["citation"]}]


def _cycle_end(payload: dict, climate_cache, crop: str, district: str | None, snap: dict | None) -> dict:
    brief = next_sowing_brief(climate_cache or {"places": {}}, crop, district)
    facts = _facts(snap) if snap else []
    facts.append({"text": brief["text"], "citation": brief["citation"]})
    payload["facts"] = facts
    payload["climate_card"] = brief
    return payload


def start_call(connection, frame, tomato_model, body: dict, gates: dict | None = None) -> dict:
    phone = normalize_phone(body.get("phone"))
    if len(phone) < 10:
        return {"error": "A phone number is required."}
    farmer = _farmer(connection, phone)
    language = body.get("language") or (farmer or {}).get("language") or "kn"
    if language not in {"en", "hi", "kn"}:
        language = "kn"
    sowing = _sowing(connection, phone) if farmer else None
    snap = _priced(frame, tomato_model, sowing["crop"], gates, sowing) if sowing else None
    summaries = []
    if farmer:
        summaries = rows(
            connection,
            """
            SELECT id, started_at, summary, sms_body, language
            FROM calls WHERE phone = ? AND summary IS NOT NULL
            ORDER BY started_at DESC LIMIT 3
            """,
            (phone,),
        )
    open_trigger = None
    if farmer:
        trigger = connection.execute(
            """
            SELECT * FROM triggers
            WHERE phone = ? AND status IN ('proposed', 'approved')
            ORDER BY id DESC LIMIT 1
            """,
            (phone,),
        ).fetchone()
        open_trigger = dict(trigger) if trigger else None
    if farmer and sowing:
        ask = "confirm_sowing"
    elif farmer:
        ask = "ask_crop_and_sow_date"
    else:
        ask = "pick_crop"
    session_id = uuid.uuid4().hex
    context = {
        "phone": phone,
        "language": language,
        "farmer_known": farmer is not None,
        "crop": sowing["crop"] if sowing else None,
    }
    connection.execute(
        "INSERT INTO sessions (id, phone, language, created_at, status, context_json) VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, phone, language, _now(), "open", json.dumps(context)),
    )
    connection.execute(
        "INSERT INTO calls (id, phone, direction, started_at, language) VALUES (?, ?, 'inbound', ?, ?)",
        (session_id, phone, _now(), language),
    )
    connection.commit()
    return {
        "session_id": session_id,
        "phone": phone,
        "language": language,
        "farmer_known": farmer is not None,
        "farmer": farmer,
        "location": _location(farmer, body),
        "active_sowing": sowing,
        "last_summaries": summaries,
        "open_trigger": open_trigger,
        "facts": _facts(snap) if snap else [],
        "crops": [CROPS[crop]["en"] for crop in CROPS],
        "ask": ask,
    }


def _session(connection, session_id: str) -> dict | None:
    row = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return dict(row) if row else None


def apply_turn(connection, frame, tomato_model, session_id: str, body: dict, gates: dict | None = None, climate_cache: dict | None = None) -> dict:
    session = _session(connection, session_id)
    if session is None:
        return {"error": "Unknown session."}
    intent = (body.get("intent") or "").strip()
    phone = session["phone"]
    language = body.get("language") or session["language"]
    farmer = _farmer(connection, phone)
    if intent == "ask_dose":
        sowing = _sowing(connection, phone)
        hit = ground(body.get("text") or "", (sowing or {}).get("crop"), connection)
        if hit:
            return {"ok": True, "grounded": True, "layer": hit["layer"], "facts": [{"text": hit["text"], "citation": hit["citation"]}]}
        return refusal("dose", body.get("text") or "ask_dose")
    if intent == "opt_in":
        if farmer is None:
            return {"error": "Save a crop and sowing before opting in."}
        connection.execute("UPDATE farmers SET opt_in = 1 WHERE phone = ?", (phone,))
        connection.commit()
        return {"ok": True, "opt_in": True, "phone": phone}
    if intent == "opt_out":
        if farmer:
            connection.execute("UPDATE farmers SET opt_in = 0 WHERE phone = ?", (phone,))
            connection.commit()
        return {"ok": True, "opt_in": False, "phone": phone}
    if intent == "confirm_sowing":
        sowing = _sowing(connection, phone)
        if sowing is None:
            return {"ok": False, "ask": "ask_crop_and_sow_date", "message": "No sowing is on record yet."}
        return {"ok": True, "same_sowing": True, "active_sowing": sowing, "message": "Same sowing confirmed."}
    if intent == "new_sowing":
        crop = canonical_crop(body.get("crop"))
        if crop is None:
            return refusal("unknown_crop", body.get("crop") or "")
        sow_date = body.get("sow_date")
        try:
            datetime.strptime(sow_date or "", "%Y-%m-%d")
        except ValueError:
            return {"error": "sow_date must be YYYY-MM-DD."}
        if farmer is None:
            district = body.get("district") or CROPS[crop]["district"]
            coords = PLACES.get(district, (None, None))
            connection.execute(
                """
                INSERT INTO farmers (phone, name, language, district, village, lat, lon, opt_in)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    phone,
                    body.get("name") or "New caller",
                    language,
                    district,
                    body.get("village"),
                    coords[0],
                    coords[1],
                ),
            )
        connection.execute(
            """
            INSERT INTO sowings (phone, crop, sow_date, quantity_qtl, in_transit, created_at)
            VALUES (?, ?, ?, ?, 0, ?)
            """,
            (phone, crop, sow_date, body.get("quantity_qtl"), _now()),
        )
        connection.commit()
        snap = _priced(frame, tomato_model, crop, gates, _sowing(connection, phone))
        payload = {"ok": True, "active_sowing": _sowing(connection, phone), "facts": _facts(snap)}
        if not snap.get("available"):
            payload["refused"] = False
            payload["message"] = "The sowing is saved. There is no mandi series for that crop yet, so no price is given."
        return payload
    if intent == "ask_price":
        crop = canonical_crop(body.get("crop"))
        if crop is None:
            sowing = _sowing(connection, phone)
            crop = sowing["crop"] if sowing else None
        if crop is None:
            return refusal("unknown_crop", body.get("crop") or "")
        sowing = _sowing(connection, phone)
        snap = _priced(frame, tomato_model, crop, gates, sowing)
        if not snap["available"]:
            return refusal("missing_series", crop)
        return {"ok": True, "facts": _facts(snap), "snapshot": {k: v for k, v in snap.items() if k != "points"}}
    if intent in {"ready_to_send", "in_transit", "sale_complete"}:
        sowing = _sowing(connection, phone)
        if sowing is None:
            return {"error": "No sowing is on record."}
        flag = 1 if intent == "in_transit" else 0
        quantity = body.get("quantity_qtl", sowing.get("quantity_qtl"))
        connection.execute(
            "UPDATE sowings SET in_transit = ?, harvested = 1, quantity_qtl = ? WHERE id = ?",
            (flag, quantity, sowing["id"]),
        )
        if intent in {"in_transit", "sale_complete"}:
            reason = "in_transit" if intent == "in_transit" else "sale_complete"
            connection.execute(
                """
                UPDATE triggers SET status = 'suppressed', suppress_reason = ?
                WHERE phone = ? AND status = 'proposed'
                """,
                (reason, phone),
            )
        connection.commit()
        updated = _sowing(connection, phone)
        payload = {"ok": True, "in_transit": bool(flag), "harvested": True, "active_sowing": updated}
        if intent == "ready_to_send":
            snap = _priced(frame, tomato_model, sowing["crop"], gates, updated)
            payload["facts"] = _facts(snap)
            return payload
        farmer = _farmer(connection, phone)
        district = (farmer or {}).get("district") or CROPS[sowing["crop"]]["district"]
        snap = _priced(frame, tomato_model, sowing["crop"], gates, updated)
        return _cycle_end(payload, climate_cache, sowing["crop"], district, snap)
    return {"error": f"Unknown intent: {intent}"}


def complete_call(connection, frame, tomato_model, session_id: str, body: dict, gates: dict | None = None) -> dict:
    session = _session(connection, session_id)
    if session is None:
        return {"error": "Unknown session."}
    phone = session["phone"]
    language = body.get("language") or session["language"]
    sowing = _sowing(connection, phone)
    crop = canonical_crop(body.get("crop")) or (sowing["crop"] if sowing else None)
    snap = _priced(frame, tomato_model, crop, gates, sowing) if crop else None
    sms = render_sms(snap, language) if snap and snap.get("available") else None
    summary = body.get("summary")
    if not summary and snap and snap.get("available"):
        summary = (
            f"{snap.get('grade_line') or ''} Advice: {snap.get('action_label') or snap['action']}."
        )
    connection.execute(
        """
        UPDATE calls
        SET ended_at = ?, summary = ?, transcript = ?, sms_body = ?, language = ?
        WHERE id = ?
        """,
        (_now(), summary, body.get("transcript"), sms, language, session_id),
    )
    connection.execute("UPDATE sessions SET status = 'closed' WHERE id = ?", (session_id,))
    connection.commit()
    return {"session_id": session_id, "summary": summary, "sms": {"language": language, "text": sms} if sms else None}
