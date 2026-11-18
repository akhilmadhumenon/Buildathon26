"""Sarvam writes the spoken line when Gemini is rate-limited or otherwise down."""

from __future__ import annotations

import json
import logging
import os

import aiohttp

from kisan.gemini_reply import (
    _ENGLISH_NUDGE,
    _without_stock_opener,
    build_prompt,
    needs_english,
    parse_gemini_reply,
    settle_reply,
)
from kisan.reference import Caller

logger = logging.getLogger("kisan.exotel")

SARVAM_CHAT_URL = "https://api.sarvam.ai/v1/chat/completions"

_REPLY_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "spoken_reply",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "say": {"type": "string"},
                "save_place": {"type": "string"},
                "end_call": {"type": "boolean"},
            },
            "required": ["say", "save_place", "end_call"],
            "additionalProperties": False,
        },
    },
}


def chat_payload(model: str, prompt: str, *, structured: bool = True) -> dict:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You write the spoken phone reply for Kisan Sahayak. "
                    "Follow the user message exactly. Return only the JSON object it describes."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 256,
        "reasoning_effort": "none",
    }
    if structured:
        payload["response_format"] = _REPLY_SCHEMA
    return payload


def _message_text(data: dict) -> str:
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    return ""


async def sarvam_reply(
    http: aiohttp.ClientSession | None,
    caller: Caller,
    turns: list[tuple[str, str]],
) -> tuple[str, str, bool] | None:
    """Same spoken reply as Gemini, using the RaithaLine facts already in the prompt."""
    key = os.environ.get("SARVAM_API_KEY", "").strip()
    if not key:
        logger.info("sarvam chat skipped, no API key")
        return None
    model = os.environ.get("SARVAM_LLM_MODEL", "sarvam-105b").strip() or "sarvam-105b"
    owns_session = http is None
    session = http or aiohttp.ClientSession()
    try:
        turns, prompt, point = await build_prompt(session, caller, turns)
        raw = await _post_sarvam(session, model, key, prompt)
        say, place, end_call = parse_gemini_reply(raw or "")
        say = _without_stock_opener(say)
        latest = next((text for role, text in reversed(turns) if role == "user"), "")
        if needs_english(latest, say):
            raw = await _post_sarvam(session, model, key, prompt + _ENGLISH_NUDGE)
            retried, place, end_call = parse_gemini_reply(raw or "")
            retried = _without_stock_opener(retried)
            if retried:
                say = retried
        return settle_reply(caller, say, place, end_call, point)
    except Exception:
        logger.exception("sarvam chat failed")
        return None
    finally:
        if owns_session:
            await session.close()


async def _post_sarvam(
    session: aiohttp.ClientSession,
    model: str,
    key: str,
    prompt: str,
) -> str | None:
    headers = {"api-subscription-key": key, "Content-Type": "application/json"}
    timeout = aiohttp.ClientTimeout(total=8)
    payload = chat_payload(model, prompt)
    async with session.post(SARVAM_CHAT_URL, json=payload, headers=headers, timeout=timeout) as response:
        body = await response.text()
        if response.status == 400:
            plain = chat_payload(model, prompt, structured=False)
            plain.pop("reasoning_effort", None)
            async with session.post(SARVAM_CHAT_URL, json=plain, headers=headers, timeout=timeout) as retry:
                body = await retry.text()
                if retry.status >= 400:
                    logger.warning("sarvam chat returned %s", retry.status)
                    return None
                data = json.loads(body)
        elif response.status >= 400:
            logger.warning("sarvam chat returned %s", response.status)
            return None
        else:
            data = json.loads(body)
    return _message_text(data) or None
