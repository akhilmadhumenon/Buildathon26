"""SQLite schema and small helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS farmers (
    phone TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    language TEXT NOT NULL,
    district TEXT,
    village TEXT,
    lat REAL,
    lon REAL,
    opt_in INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sowings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL,
    crop TEXT NOT NULL,
    sow_date TEXT NOT NULL,
    quantity_qtl REAL,
    in_transit INTEGER NOT NULL DEFAULT 0,
    harvested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calls (
    id TEXT PRIMARY KEY,
    phone TEXT,
    direction TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    language TEXT,
    summary TEXT,
    transcript TEXT,
    sms_body TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    phone TEXT,
    language TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    context_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prices (
    crop TEXT NOT NULL,
    role TEXT NOT NULL,
    market TEXT NOT NULL,
    district TEXT NOT NULL,
    date TEXT NOT NULL,
    arrival_tonnes REAL,
    min_price REAL,
    max_price REAL,
    modal_price REAL,
    PRIMARY KEY (crop, role, date)
);

CREATE TABLE IF NOT EXISTS triggers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL,
    crop TEXT NOT NULL,
    mandi TEXT NOT NULL,
    reason TEXT NOT NULL,
    citations TEXT NOT NULL,
    status TEXT NOT NULL,
    suppress_reason TEXT,
    created_at TEXT NOT NULL,
    result TEXT
);

CREATE TABLE IF NOT EXISTS gate_prices (
    crop TEXT PRIMARY KEY,
    modal REAL NOT NULL,
    min_price REAL,
    max_price REAL,
    entered_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS findings (
    kind TEXT NOT NULL,
    item_key TEXT NOT NULL,
    payload TEXT NOT NULL,
    source TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    PRIMARY KEY (kind, item_key)
);

CREATE TABLE IF NOT EXISTS agent_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    claim TEXT NOT NULL,
    citation TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS findings (
            kind TEXT NOT NULL,
            item_key TEXT NOT NULL,
            payload TEXT NOT NULL,
            source TEXT NOT NULL,
            retrieved_at TEXT NOT NULL,
            PRIMARY KEY (kind, item_key)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS gate_prices (
            crop TEXT PRIMARY KEY,
            modal REAL NOT NULL,
            min_price REAL,
            max_price REAL,
            entered_at TEXT NOT NULL
        )
        """
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(sowings)")}
    if columns and "harvested" not in columns:
        connection.execute("ALTER TABLE sowings ADD COLUMN harvested INTEGER NOT NULL DEFAULT 0")
    if columns:
        connection.execute("UPDATE sowings SET harvested = 1 WHERE in_transit = 1 AND harvested = 0")
    connection.commit()


def connect(path: Path | str) -> sqlite3.Connection:
    # FastAPI resolves a dependency and then runs the handler on another worker thread.
    connection = sqlite3.connect(path, check_same_thread=False, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    _migrate(connection)
    return connection


def init_db(path: Path | str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as connection:
        connection.executescript(SCHEMA)


def rows(connection: sqlite3.Connection, query: str, params: tuple = ()) -> list[dict]:
    return [dict(row) for row in connection.execute(query, params).fetchall()]
