"""Read the caller's number from a phone leg or from the browser token."""

from __future__ import annotations

import json
import os
from typing import Any

from kisan.db import normalize_phone


def resolve_phone(ctx: Any, participant: Any | None) -> str:
    candidates: list[str] = []
    if participant is not None:
        attributes = getattr(participant, "attributes", None) or {}
        candidates.append(attributes.get("sip.phoneNumber", ""))
        candidates.append(attributes.get("caller_phone", ""))
        candidates.append(phone_from_json(getattr(participant, "metadata", None)))
    job = getattr(ctx, "job", None)
    candidates.append(phone_from_json(getattr(job, "metadata", None)))
    room = getattr(ctx, "room", None)
    candidates.append(phone_from_json(getattr(room, "metadata", None)))
    candidates.append(os.environ.get("DEMO_CALLER_PHONE", ""))
    for candidate in candidates:
        phone = normalize_phone(candidate)
        if phone:
            return phone
    return ""


def phone_from_json(raw: str | None) -> str:
    if not raw:
        return ""
    text = raw.strip()
    if not text.startswith("{"):
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("phone") or payload.get("caller_phone") or "")
