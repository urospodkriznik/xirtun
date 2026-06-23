"""Tracking weekly-review runs in the ``runs`` table.

Used for idempotency (don't run twice for the same week) and startup catch-up (run
once if a scheduled slot was missed while the process was down).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime


def start(conn: sqlite3.Connection, now: datetime) -> int:
    """Record a run as started ('running') and return its id."""
    cursor = conn.execute(
        "INSERT INTO runs (kind, started_at, status) VALUES ('weekly', ?, 'running')",
        (now.isoformat(),),
    )
    conn.commit()
    return cursor.lastrowid


def finish(conn: sqlite3.Connection, run_id: int, now: datetime, status: str) -> None:
    """Mark a run finished with the given status ('ok' or 'error')."""
    conn.execute(
        "UPDATE runs SET finished_at = ?, status = ? WHERE id = ?",
        (now.isoformat(), status, run_id),
    )
    conn.commit()


def last_ok_at(conn: sqlite3.Connection) -> datetime | None:
    """When the most recent successful weekly run started, or None if there's been none."""
    row = conn.execute(
        "SELECT MAX(started_at) AS t FROM runs WHERE kind = 'weekly' AND status = 'ok'"
    ).fetchone()
    return datetime.fromisoformat(row["t"]) if row and row["t"] else None
