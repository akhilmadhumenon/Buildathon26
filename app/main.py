"""HTTP API for the voice agent and the mandi desk."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.agents import weekly_plan
from app.agmarknet import fetch_pending, reader_available
from app.calls import apply_turn, complete_call, start_call
from app.climate import load as load_climate
from app.climate import nearest_screened_district, suitability, worldclim_shifts
from app.crops import CROPS, canonical_crop
from app.db import connect, database_url, open_store, rows
from app.demo import ensure_demo_maize
from app.forecast import fit_tomato, round_metric, snapshot
from app.prices import load_price_frame, read_prices, write_prices
from app.triggers import queue_callback, rebuild, set_status
from app.voice import answer as voice_answer
from app.voice import build_cards
from app.weather import load as load_weather

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("RAITHA_DB", ROOT / "data" / "raitha.db"))
_sync_lock = threading.Lock()
_sync_state = {
    "state": "idle",
    "done": 0,
    "total": 0,
    "crop": None,
    "year": None,
    "month": None,
    "fetched": [],
    "failed": [],
    "latest": {},
    "detail": None,
}
_weather_started = False

@asynccontextmanager
async def lifespan(_app: FastAPI):
    _load()
    yield


app = FastAPI(title="RaithaLine", version="0.1.0", lifespan=lifespan)


class InboundCall(BaseModel):
    phone: str
    language: str | None = None
    lat: float | None = None
    lon: float | None = None


class Turn(BaseModel):
    intent: str
    crop: str | None = None
    sow_date: str | None = None
    quantity_qtl: float | None = None
    district: str | None = None
    village: str | None = None
    name: str | None = None
    text: str | None = None
    language: str | None = None


class CompleteCall(BaseModel):
    transcript: str | None = None
    summary: str | None = None
    language: str | None = None
    crop: str | None = None


class OutboundResult(BaseModel):
    outcome: str
    note: str | None = None


class WeeklyPlan(BaseModel):
    crop: str
    sowing_date: str | None = None
    district: str | None = None
    soil_type: str | None = None
    question: str | None = None


class VoiceQuestion(BaseModel):
    text: str = ""
    phone: str | None = None
    language: str | None = None
    crop: str | None = None
    place: str | None = None
    sown_on: str | None = None


class Suppress(BaseModel):
    reason: str = "admin"


class SaleAnswer(BaseModel):
    answer: str


class GatePrice(BaseModel):
    crop: str
    modal: float
    min_price: float | None = None
    max_price: float | None = None


def _db():
    if not database_url() and not DB_PATH.exists():
        raise HTTPException(status_code=503, detail="Database is missing. Run scripts/bootstrap.py.")
    connection = open_store(DB_PATH)
    try:
        yield connection
    finally:
        connection.close()


def _sync_snapshot() -> dict:
    with _sync_lock:
        return dict(_sync_state)


def _sync_update(**fields) -> None:
    with _sync_lock:
        _sync_state.update(fields)


def _latest_days(frame) -> dict:
    if frame is None or frame.empty:
        return {}
    primary = frame[frame["role"] == "primary"]
    latest = {}
    for crop, part in primary.groupby("crop"):
        latest[str(crop)] = pd.Timestamp(part["date"].max()).date().isoformat()
    return latest


def _reload_prices() -> dict:
    frame = load_price_frame()
    with connect(DB_PATH) as connection:
        write_prices(connection, frame)
        connection.commit()
    app.state.prices = frame
    app.state.tomato = fit_tomato(frame)
    _refresh_voice()
    return _latest_days(frame)


def _run_sync() -> None:
    try:
        def progress(event: dict) -> None:
            _sync_update(
                done=event["done"],
                total=event["total"],
                crop=event["crop"],
                year=event["year"],
                month=event["month"],
            )

        result = fetch_pending(refresh_open=True, progress=progress)
        latest = _reload_prices()
        failed = result["failed"]
        _sync_update(
            state="error" if failed and not result["fetched"] else "done",
            done=len(result["fetched"]) + len(failed),
            fetched=result["fetched"],
            failed=failed,
            latest=latest,
            crop=None,
            detail=failed[0]["detail"] if failed and not result["fetched"] else None,
        )
    except Exception as exc:  # noqa: BLE001 - the desk shows the failure instead of hanging
        _sync_update(state="error", detail=str(exc)[:180], crop=None)


def _load() -> None:
    if DB_PATH.exists():
        with connect(DB_PATH) as connection:
            app.state.prices = read_prices(connection)
            if not database_url():
                app.state.gates = {row["crop"]: row for row in rows(connection, "SELECT * FROM gate_prices")}
    else:
        app.state.prices = pd.DataFrame()
    if database_url():
        with open_store(DB_PATH) as connection:
            app.state.gates = {row["crop"]: row for row in rows(connection, "SELECT * FROM gate_prices")}
    elif not DB_PATH.exists():
        app.state.tomato = {"ready": False, "reason": "Database is missing."}
        app.state.weather = {"places": {}}
        app.state.climate = {"places": {}}
        app.state.gates = {}
        app.state.voice_cards = {}
        return
    app.state.tomato = fit_tomato(app.state.prices)
    app.state.weather = load_weather()
    app.state.climate = load_climate()
    with open_store(DB_PATH) as connection:
        ensure_demo_maize(connection)
    _refresh_voice()
    _keep_weather_fresh()


@app.get("/")
def desk():
    return FileResponse(ROOT / "static" / "admin.html")


@app.get("/v1/health")
def health():
    prices = app.state.prices
    crops = sorted(prices["crop"].unique()) if prices is not None and not prices.empty else []
    return {"ok": True, "crops_loaded": crops, "tomato_model": bool(app.state.tomato.get("ready"))}


@app.post("/v1/voice/answer")
def voice(body: VoiceQuestion, connection=Depends(_db)):
    return voice_answer(
        cards=getattr(app.state, "voice_cards", {}),
        weather=getattr(app.state, "weather", {}),
        text=body.text,
        language=body.language,
        crop=body.crop,
        place=body.place,
        phone=body.phone,
        sown_on=body.sown_on,
        connection=connection,
    )


@app.post("/v1/calls/inbound")
def inbound(body: InboundCall, connection=Depends(_db)):
    result = start_call(connection, app.state.prices, app.state.tomato, body.model_dump(), app.state.gates)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/v1/calls/{session_id}/turn")
def turn(session_id: str, body: Turn, connection=Depends(_db)):
    result = apply_turn(
        connection,
        app.state.prices,
        app.state.tomato,
        session_id,
        body.model_dump(),
        app.state.gates,
        app.state.climate,
    )
    if result.get("error") == "Unknown session.":
        raise HTTPException(status_code=404, detail=result["error"])
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    changed = body.intent in {"opt_in", "opt_out", "new_sowing", "in_transit", "ready_to_send", "sale_complete"}
    if changed and not result.get("refused"):
        rebuild(connection, app.state.prices, app.state.tomato, app.state.weather, app.state.gates)
    return result


@app.post("/v1/calls/{session_id}/complete")
def complete(session_id: str, body: CompleteCall, connection=Depends(_db)):
    result = complete_call(connection, app.state.prices, app.state.tomato, session_id, body.model_dump(), app.state.gates)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


def _with_citations(found: list[dict]) -> list[dict]:
    for item in found:
        try:
            item["citations"] = json.loads(item["citations"])
        except (TypeError, json.JSONDecodeError):
            item["citations"] = []
    return found


@app.get("/v1/outbound/pending")
def pending(connection=Depends(_db)):
    return {
        "calls": _with_citations(rows(
            connection,
            "SELECT * FROM triggers WHERE status = 'approved' ORDER BY id",
        ))
    }


@app.post("/v1/outbound/{trigger_id}/result")
def outbound_result(trigger_id: int, body: OutboundResult, connection=Depends(_db)):
    updated = set_status(connection, trigger_id, "called", result=body.outcome if not body.note else f"{body.outcome}: {body.note}")
    if updated is None:
        raise HTTPException(status_code=404, detail="Unknown trigger.")
    return updated


@app.post("/v1/plans/weekly")
def plan(body: WeeklyPlan, connection=Depends(_db)):
    result = weekly_plan(
        app.state.prices,
        app.state.tomato,
        app.state.weather,
        app.state.climate,
        body.model_dump(),
        app.state.gates,
        connection,
    )
    if not result.get("refused"):
        plan_id = f"plan-{result['crop']}-{result['district']}"
        for claim in result["claims"]:
            connection.execute(
                """
                INSERT INTO agent_logs (plan_id, agent, claim, citation, created_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                """,
                (plan_id, claim["agent"], claim["claim"], claim["citation"]),
            )
        connection.commit()
    return result


@app.get("/v1/admin/board")
def board(crop: str = "tomato"):
    chosen = canonical_crop(crop) or "tomato"
    snap = snapshot(app.state.prices, chosen, app.state.tomato, (app.state.gates or {}).get(chosen))
    evaluation = None
    if chosen == "tomato" and app.state.tomato.get("ready"):
        metrics = app.state.tomato["metrics"]
        evaluation = {
            "holdout_start": metrics["holdout_start"],
            "holdout_end": metrics["holdout_end"],
            "n": metrics["n"],
            "wape_model_pct": round_metric(metrics["wape_model"]),
            "wape_persistence_pct": round_metric(metrics["wape_persistence"]),
            "wape_seasonal_pct": round_metric(metrics["wape_seasonal"]),
            "beats_both": metrics["beats_both"],
            "examples": app.state.tomato["examples"],
        }
    return {
        "crops": [{"id": key, "label": CROPS[key]["en"]} for key in CROPS],
        "snapshot": snap,
        "evaluation": evaluation,
    }


@app.get("/v1/admin/climate")
def climate_board(district: str = "Kolar"):
    screened = nearest_screened_district(district)
    cards = [card for card in suitability(app.state.climate) if card["district"] == screened]
    cards.sort(key=lambda card: card["change_high"])
    shifts = worldclim_shifts().get("places", {}).get(screened, {})
    return {
        "district": screened,
        "source": worldclim_shifts().get("source"),
        "ssp245": shifts.get("ssp245"),
        "ssp585": shifts.get("ssp585"),
        "cards": [
            {
                "crop": card["crop"],
                "crop_en": card["crop_en"],
                "today": card["scenarios"]["today"]["score"],
                "moderate": card["scenarios"]["ssp245_2041_2060"]["score"],
                "high": card["scenarios"]["ssp585_2041_2060"]["score"],
                "change_high": card["change_high"],
            }
            for card in cards
        ],
    }


@app.get("/v1/admin/farmers")
def farmers(connection=Depends(_db)):
    people = rows(connection, "SELECT * FROM farmers ORDER BY phone")
    for person in people:
        sowing = connection.execute(
            "SELECT * FROM sowings WHERE phone = ? ORDER BY id DESC LIMIT 1",
            (person["phone"],),
        ).fetchone()
        person["sowing"] = dict(sowing) if sowing else None
        person["calls"] = rows(
            connection,
            "SELECT id, started_at, summary, sms_body, language FROM calls WHERE phone = ? ORDER BY started_at DESC LIMIT 5",
            (person["phone"],),
        )
    return {"farmers": people}


@app.get("/v1/admin/triggers")
def triggers(connection=Depends(_db)):
    found = _with_citations(rows(connection, "SELECT * FROM triggers ORDER BY id DESC"))
    return {"triggers": found}


def _kisan_url() -> str:
    raw = (os.environ.get("KISAN_URL") or os.environ.get("RAILWAY_SERVICE_KISANCALL_URL") or "").strip().rstrip("/")
    if raw and "://" not in raw:
        raw = "https://" + raw
    return raw


@app.post("/v1/admin/triggers/{trigger_id}/dial")
def dial(trigger_id: int, connection=Depends(_db)):
    current = connection.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
    if current is None:
        raise HTTPException(status_code=404, detail="Unknown trigger.")
    if current["status"] == "suppressed":
        raise HTTPException(status_code=400, detail="This call is held back.")
    base = _kisan_url()
    if not base:
        raise HTTPException(status_code=503, detail="KisanCall is not configured.")
    person = connection.execute(
        "SELECT language FROM farmers WHERE phone = ?",
        (current["phone"],),
    ).fetchone()
    language = person["language"] if person and person["language"] in {"en", "hi", "kn"} else "en"
    payload = json.dumps({
        "phone": current["phone"],
        "crop": current["crop"],
        "district": "Bengaluru",
        "language": language,
        "trigger_id": trigger_id,
        "mandi": current["mandi"],
    }).encode()
    request = urllib.request.Request(
        base + "/exotel/dial",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:180]
        raise HTTPException(status_code=502, detail=detail or "KisanCall did not place the call.") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail="KisanCall did not answer.") from exc
    updated = set_status(connection, trigger_id, "called", result="dialed")
    return {"trigger": updated, "call": body}


@app.post("/v1/admin/triggers/{trigger_id}/answer")
def record_answer(trigger_id: int, body: SaleAnswer, connection=Depends(_db)):
    choice = body.answer.strip().lower()
    if choice not in {"yes", "no"}:
        raise HTTPException(status_code=400, detail="Answer must be yes or no.")
    current = connection.execute("SELECT id FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
    if current is None:
        raise HTTPException(status_code=404, detail="Unknown trigger.")
    result = "Farmer said yes." if choice == "yes" else "Farmer said no."
    updated = set_status(connection, trigger_id, choice, result=result)
    return {"trigger": updated}


@app.post("/v1/admin/triggers/{trigger_id}/approve")
def approve(trigger_id: int, connection=Depends(_db)):
    current = connection.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
    if current is None:
        raise HTTPException(status_code=404, detail="Unknown trigger.")
    if current["status"] != "proposed":
        raise HTTPException(status_code=400, detail="Only a proposed call can be approved.")
    return set_status(connection, trigger_id, "approved")


@app.post("/v1/admin/triggers/{trigger_id}/suppress")
def suppress(trigger_id: int, body: Suppress, connection=Depends(_db)):
    current = connection.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
    if current is None:
        raise HTTPException(status_code=404, detail="Unknown trigger.")
    return set_status(connection, trigger_id, "suppressed", suppress_reason=body.reason)


@app.post("/v1/admin/triggers/{trigger_id}/callback")
def callback(trigger_id: int, connection=Depends(_db)):
    result = queue_callback(connection, trigger_id, app.state.prices, app.state.tomato, app.state.gates)
    if result.get("error") == "missing":
        raise HTTPException(status_code=404, detail="Unknown trigger.")
    if result.get("error") == "proposed":
        raise HTTPException(status_code=400, detail="Approve this call instead of queueing another.")
    if result.get("error") == "already":
        raise HTTPException(status_code=409, detail="This farmer is already waiting to be dialed.")
    return result["trigger"]


@app.get("/v1/admin/sync")
def sync_status():
    return _sync_snapshot()


@app.post("/v1/admin/sync", status_code=202)
def sync_prices():
    if not DB_PATH.exists():
        raise HTTPException(status_code=503, detail="Database is missing. Run scripts/bootstrap.py.")
    if not reader_available():
        raise HTTPException(status_code=503, detail="captcha_reader")
    with _sync_lock:
        if _sync_state["state"] == "running":
            return dict(_sync_state)
        _sync_state.update(
            {
                "state": "running",
                "done": 0,
                "total": 0,
                "crop": None,
                "year": None,
                "month": None,
                "fetched": [],
                "failed": [],
                "latest": {},
                "detail": None,
            }
        )
    threading.Thread(target=_run_sync, name="agmarknet-sync", daemon=True).start()
    return _sync_snapshot()


@app.post("/v1/admin/triggers/rebuild")
def rebuild_queue(connection=Depends(_db)):
    return {"triggers": rebuild(connection, app.state.prices, app.state.tomato, app.state.weather, app.state.gates)}


def _refresh_gates(connection) -> None:
    app.state.gates = {row["crop"]: row for row in rows(connection, "SELECT * FROM gate_prices")}
    _refresh_voice()


def _refresh_voice() -> None:
    app.state.voice_cards = build_cards(app.state.prices, app.state.tomato, app.state.gates)


def _keep_weather_fresh() -> None:
    global _weather_started
    if _weather_started:
        return
    _weather_started = True

    def loop() -> None:
        while True:
            time.sleep(3 * 60 * 60)
            try:
                app.state.weather = load_weather()
            except Exception:
                continue

    threading.Thread(target=loop, name="weather-refresh", daemon=True).start()


@app.post("/v1/admin/gate")
def save_gate(body: GatePrice, connection=Depends(_db)):
    crop = canonical_crop(body.crop)
    if crop is None:
        raise HTTPException(status_code=400, detail="Unknown crop.")
    if body.modal <= 0:
        raise HTTPException(status_code=400, detail="The gate modal has to be above zero.")
    if body.min_price is not None and body.max_price is not None and body.min_price > body.max_price:
        raise HTTPException(status_code=400, detail="The low grade cannot sit above the high grade.")
    entered = datetime.now(timezone.utc).isoformat()
    connection.execute(
        """
        INSERT INTO gate_prices (crop, modal, min_price, max_price, entered_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(crop) DO UPDATE SET
            modal = excluded.modal,
            min_price = excluded.min_price,
            max_price = excluded.max_price,
            entered_at = excluded.entered_at
        """,
        (crop, body.modal, body.min_price, body.max_price, entered),
    )
    connection.commit()
    _refresh_gates(connection)
    snap = snapshot(app.state.prices, crop, app.state.tomato, app.state.gates.get(crop))
    return {"crop": crop, "snapshot": {key: value for key, value in snap.items() if key != "points"}}


@app.delete("/v1/admin/gate/{crop}")
def clear_gate(crop: str, connection=Depends(_db)):
    chosen = canonical_crop(crop)
    if chosen is None:
        raise HTTPException(status_code=400, detail="Unknown crop.")
    connection.execute("DELETE FROM gate_prices WHERE crop = ?", (chosen,))
    connection.commit()
    _refresh_gates(connection)
    snap = snapshot(app.state.prices, chosen, app.state.tomato, None)
    return {"crop": chosen, "snapshot": {key: value for key, value in snap.items() if key != "points"}}
