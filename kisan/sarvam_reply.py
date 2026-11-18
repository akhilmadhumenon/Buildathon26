"""Sarvam writes the spoken line when Gemini is rate-limited or otherwise down."""

from __future__ import annotations

import json
import logging
import os

import aiohttp

from kisan.gemini_reply import (
    _without_stock_opener,
    build_prompt,
    language_nudge,
    parse_gemini_reply,
    settle_reply,
)
from kisan.reference import Caller

logger = logging.getLogger("kisan.exotel")

_V1_CHAT_URL = "https://api.sarvam.ai/v1/chat/completions"
_V2_CHAT_URL = "https://api.sarvam.ai/v2/chat/completions"
_V1_MODELS = {"sarvam-105b", "sarvam-105b-conversations"}


def chat_url(model: str) -> str:
    """Conversations stays on v1. Gemma and the other open models are on v2."""
    if model in _V1_MODELS:
        return _V1_CHAT_URL
    return _V2_CHAT_URL

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
        # null disables thinking. "none" is rejected, and the default spends the reply budget on reasoning.
        "reasoning_effort": None,
    }
    if structured:
        payload["response_format"] = _REPLY_SCHEMA
    return payload


def _message_text(data: dict) -> str:
    """The reply text, whether Sarvam returned a string, a JSON object, or a list of parts."""
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message") or {}
    text = _content_text(message.get("content"))
    if text:
        return text
    reasoning = message.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning.strip().startswith("{"):
        say, _, _ = parse_gemini_reply(reasoning)
        if say:
            return reasoning.strip()
    return ""


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("text"):
                parts.append(str(part["text"]))
            else:
                parts.append(_content_text(part))
        return "\n".join(part for part in parts if part).strip()
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
    model = os.environ.get("SARVAM_LLM_MODEL", "gemma4").strip() or "gemma4"
    owns_session = http is None
    session = http or aiohttp.ClientSession()
    try:
        turns, prompt, point = await build_prompt(session, caller, turns)
        raw = await _post_sarvam(session, model, key, prompt)
        say, place, end_call = parse_gemini_reply(raw or "")
        say = _without_stock_opener(say)
        latest = next((text for role, text in reversed(turns) if role == "user"), "")
        nudge = language_nudge(latest, say)
        if nudge:
            raw = await _post_sarvam(session, model, key, prompt + nudge)
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
    text = await _complete(session, model, key, prompt, structured=True)
    if text:
        return text
    logger.warning("sarvam chat had no speakable text, retrying without a schema")
    return await _complete(session, model, key, prompt, structured=False)


async def _complete(
    session: aiohttp.ClientSession,
    model: str,
    key: str,
    prompt: str,
    *,
    structured: bool,
) -> str | None:
    headers = {"api-subscription-key": key, "Content-Type": "application/json"}
    timeout = aiohttp.ClientTimeout(total=8)
    payload = chat_payload(model, prompt, structured=structured)
    async with session.post(chat_url(model), json=payload, headers=headers, timeout=timeout) as response:
        body = await response.text()
        if response.status >= 400:
            logger.warning("sarvam chat returned %s", response.status)
            return None
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            logger.warning("sarvam chat returned text that was not JSON")
            return None
    return _message_text(data) or None
