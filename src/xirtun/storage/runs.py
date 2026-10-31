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


def first_logged_at(conn: sqlite3.Connection) -> datetime | None:
    """When the person first logged anything (a meal, symptom or workout), or None.

    Uses `logged_at` — when they typed it in — not `occurred_at`, which the model may
    place days earlier ("last Tuesday I had...") and would make a brand-new user look
    like they've been here a week.
    """
    firsts = []
    for table in ("meals", "symptoms", "exercises"):
        row = conn.execute(f"SELECT MIN(logged_at) AS t FROM {table}").fetchone()  # fixed names
        if row and row["t"]:
            firsts.append(datetime.fromisoformat(row["t"]))
    return min(firsts) if firsts else None


def review_baseline(conn: sqlite3.Connection) -> datetime | None:
    """The moment the next review's 7 days are counted from: the last successful
    review, or — for someone who has never had one — their first logged entry. Without
    that second rule a new person's database has no review on record, so the very first
    daily tick would review a single day of food. None means there is nothing to review
    yet (no review and nothing logged)."""
    return last_ok_at(conn) or first_logged_at(conn)
