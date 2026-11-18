"""Turn a call transcript into a row the next call can read."""

from __future__ import annotations

import re
from typing import Iterable


def extract_turns(items: Iterable[object]) -> list[tuple[str, str]]:
    turns: list[tuple[str, str]] = []
    for item in items:
        role = getattr(item, "role", None)
        role = getattr(role, "value", role)
        if role not in ("user", "assistant"):
            continue
        text = _text_of(item).strip()
        if text:
            turns.append((role, text))
    return turns


def render_summary(turns: list[tuple[str, str]]) -> tuple[str, str, str]:
    questions = [text for role, text in turns if role == "user"]
    answers = [text for role, text in turns if role == "assistant"]
    questions_text = " | ".join(questions) if questions else "(caller hung up before asking)"
    answers_text = " | ".join(answers) if answers else "(no answer spoken)"
    summary = f"Farmer asked: {questions_text}. Agent answered: {answers_text}."
    return summary, questions_text, answers_text


def tokenize(text: str) -> set[str]:
    return {token for token in re.findall(r"\w+", (text or "").lower(), flags=re.UNICODE) if len(token) > 1}


def overlap_score(question: str, previous: str) -> int:
    left = tokenize(question)
    right = tokenize(previous)
    if not left or not right:
        return 0
    return len(left & right)


def _text_of(item: object) -> str:
    direct = getattr(item, "text_content", None)
    if isinstance(direct, str) and direct.strip():
        return direct
    content = getattr(item, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            else:
                piece = getattr(part, "text", None)
                if isinstance(piece, str):
                    parts.append(piece)
        return " ".join(part for part in parts if part)
    return ""
