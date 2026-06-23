"""SQLite access: open the database, create the schema, and key/value helpers.

Used as a context manager (``with get_connection(path) as conn:``), the sqlite3
connection commits on a clean exit and rolls back on an exception.

No ORM — SQL is written directly (see docs/decisions.md ADR-002). Always use ``?``
placeholders for values; never string-format data into SQL.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# The schema mirrors docs/architecture.md. CREATE IF NOT EXISTS makes init_db
# safe to call on every startup.
SCHEMA = """
CREATE TABLE IF NOT EXISTS meals (
    id          INTEGER PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    logged_at   TEXT NOT NULL,
    raw_text    TEXT NOT NULL,
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS meal_items (
    id         INTEGER PRIMARY KEY,
    meal_id    INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    quantity_g REAL,
    calories   REAL,
    protein_g  REAL,
    fat_g      REAL,
    carbs_g    REAL,
    tags       TEXT
);

CREATE TABLE IF NOT EXISTS symptoms (
    id          INTEGER PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    logged_at   TEXT NOT NULL,
    type        TEXT NOT NULL,
    severity    INTEGER,
    duration    TEXT,
    raw_text    TEXT NOT NULL,
    tags        TEXT
);

CREATE TABLE IF NOT EXISTS pending (
    chat_id    TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    draft      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


def get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row              # rows act like dicts: row["name"]
    conn.execute("PRAGMA foreign_keys = ON")    # enforce the REFERENCES above
    return conn


def init_db(db_path: Path) -> None:
    """Create all tables if they don't exist yet."""
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)


# --- key/value helpers (e.g. the Telegram update offset) ---

def kv_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT v FROM kv WHERE k = ?", (key,)).fetchone()
    return row["v"] if row else None


def kv_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    # "upsert": insert, or update v if the key already exists.
    conn.execute(
        "INSERT INTO kv (k, v) VALUES (?, ?) "
        "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
        (key, value),
    )
    conn.commit()
