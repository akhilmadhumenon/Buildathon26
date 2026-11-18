"""Local memory for anything a live call managed to learn.

The next request reads this table before it spends a network call.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.db import rows


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def recall(connection, kind: str, key: str) -> dict | None:
    found = rows(connection, "SELECT * FROM findings WHERE kind = ? AND item_key = ?", (kind, key))
    if not found:
        return None
    item = found[0]
    try:
        payload = json.loads(item["payload"])
    except (TypeError, json.JSONDecodeError):
        payload = None
    return {
        "kind": item["kind"],
        "item_key": item["item_key"],
        "payload": payload,
        "source": item["source"],
        "retrieved_at": item["retrieved_at"],
    }


def remember(connection, kind: str, key: str, payload, source: str) -> None:
    connection.execute(
        """
        INSERT INTO findings (kind, item_key, payload, source, retrieved_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(kind, item_key) DO UPDATE SET
            payload = excluded.payload,
            source = excluded.source,
            retrieved_at = excluded.retrieved_at
        """,
        (kind, key, json.dumps(payload, ensure_ascii=False), source, _now()),
    )
    connection.commit()
