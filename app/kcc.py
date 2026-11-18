"""Kisan Call Centre quotes. The local file is read first. A live pull is optional."""

from __future__ import annotations

import json
import os
import re
import ssl
import urllib.parse
import urllib.request
from pathlib import Path

from app.crops import canonical_crop
from app.layer import recall, remember

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "data" / "kcc.json"
RESOURCE = "cef25fe2-9231-4128-8aec-2c948fedd43f"
SOURCE = (
    "Department of Agriculture and Farmers Welfare, Kisan Call Centre transcripts, "
    f"data.gov.in resource {RESOURCE}."
)
STOP = {
    "the", "a", "an", "of", "to", "for", "in", "on", "and", "or", "is", "are",
    "what", "how", "should", "my", "we", "do", "does", "about", "with", "from",
    "this", "that", "crop", "farmer", "asked", "query", "information", "regarding",
    "please", "tell", "give", "can", "you", "your", "our",
}
_LOCAL: list[dict] | None = None
CTX = ssl._create_unverified_context()


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z]{3,}", (text or "").lower())
    return {word for word in words if word not in STOP}


def local_records() -> list[dict]:
    global _LOCAL
    if _LOCAL is None:
        if PATH.exists():
            _LOCAL = json.loads(PATH.read_text()).get("records") or []
        else:
            _LOCAL = []
    return _LOCAL


def _merged(connection) -> list[dict]:
    records = list(local_records())
    if connection is None:
        return records
    extra = recall(connection, "kcc_extra", "appended")
    if not extra or not extra.get("payload"):
        return records
    known = {item["id"] for item in records}
    for item in extra["payload"]:
        if item.get("id") and item["id"] not in known:
            records.append(item)
    return records


def _best(records: list[dict], question: str, crop: str | None, minimum: float) -> dict | None:
    wanted = _tokens(question)
    if crop:
        wanted.discard(crop)
    if len(wanted) < 1:
        return None
    best = None
    best_score = 0.0
    for record in records:
        if crop and record.get("crop") != crop:
            continue
        hay = _tokens(f"{record.get('query', '')} {record.get('query_type', '')}")
        overlap = wanted & hay
        if len(overlap) < 2:
            continue
        score = len(overlap) / len(wanted)
        if score > best_score:
            best_score = score
            best = record
    if best is None or best_score < minimum:
        return None
    return best


def _shape(record: dict, layer: str) -> dict:
    text = (
        f"Kisan Call Centre record {record['id']} ({record.get('district') or 'Karnataka'}, "
        f"{record.get('query_type') or 'advisory'}, {record.get('created_on') or 'date not stored'}). "
        f"Question: {record['query']} Archived answer: {record['answer']} "
        "This is the call-centre record, not a new dose. Confirm it with the Krishi Vigyan Kendra before acting."
    )
    return {
        "layer": layer,
        "id": record["id"],
        "text": text,
        "citation": f"{SOURCE} Record {record['id']}. Crop: {record.get('crop_label') or record.get('crop')}.",
        "record": record,
    }


def _live_rows(crop: str) -> list[dict]:
    key = os.environ.get("DATA_GOV_API_KEY")
    if not key:
        return []
    params = {
        "api-key": key,
        "format": "json",
        "limit": 20,
        "filters[StateName]": "KARNATAKA",
        "filters[Crop]": crop,
    }
    url = f"https://api.data.gov.in/resource/{RESOURCE}?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=8, context=CTX) as response:
            body = json.loads(response.read().decode())
    except (OSError, json.JSONDecodeError, TimeoutError):
        return []
    found = []
    for index, row in enumerate(body.get("records") or []):
        query = (row.get("QueryText") or row.get("querytext") or "").strip()
        answer = (row.get("KccAns") or row.get("kccans") or "").strip()
        if len(query) < 12 or len(answer) < 12:
            continue
        found.append(
            {
                "id": f"kcc-live-{crop}-{index}",
                "crop": canonical_crop(crop) or crop,
                "crop_label": row.get("Crop") or row.get("crop") or crop,
                "district": row.get("DistrictName") or row.get("districtname") or "",
                "query_type": row.get("QueryType") or row.get("querytype") or "",
                "query": query[:600],
                "answer": answer[:800],
                "created_on": row.get("CreatedOn") or row.get("createdon") or "",
                "source": SOURCE,
            }
        )
    return found


def ground(question: str | None, crop: str | None, connection=None, minimum: float = 0.4) -> dict | None:
    """Local KCC first. A configured data.gov.in key is the only live step, and its rows are stored."""
    if not question or not question.strip():
        return None
    chosen = canonical_crop(crop) if crop else None
    hit = _best(_merged(connection), question, chosen, minimum)
    if hit:
        return _shape(hit, "deterministic")
    fresh = _live_rows(chosen or "")
    if not fresh or connection is None:
        return None
    existing = recall(connection, "kcc_extra", "appended")
    stored = list(existing["payload"]) if existing and existing.get("payload") else []
    known = {item["id"] for item in stored}
    for item in fresh:
        if item["id"] not in known:
            stored.append(item)
    remember(connection, "kcc_extra", "appended", stored, SOURCE)
    hit = _best(stored, question, chosen, minimum)
    if hit:
        return _shape(hit, "appended")
    return None
