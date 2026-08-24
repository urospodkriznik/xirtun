"""Keeping every weekly report the agent writes.

Reports were previously sent to Telegram and then gone — the `runs` table recorded
only that a run happened. They are the most considered analysis this app produces,
so they are now stored verbatim: `/exportdeepdive` hands them to a bigger model, and
a year of them is the difference between "here are your last 90 days" and "here is
how your last year was read at the time".

Saved when the report is produced, not when it is delivered — a manual run holds its
report back until the follow-up Q&A is answered, and that report is worth keeping
either way.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any


def save(
    conn: sqlite3.Connection,
    report: str,
    *,
    manner: str,
    questions: list[str] | None = None,
    now: datetime | None = None,
) -> int:
    """Store one weekly report. Returns its new id."""
    now = now or datetime.now().astimezone()
    cursor = conn.execute(
        "INSERT INTO weekly_reports (created_at, manner, report, questions) VALUES (?, ?, ?, ?)",
        (now.isoformat(), manner, report, json.dumps(questions or [])),
    )
    conn.commit()
    return cursor.lastrowid


def _row(row: sqlite3.Row) -> dict[str, Any]:
    report = dict(row)
    report["questions"] = json.loads(report["questions"]) if report.get("questions") else []
    return report


def since(conn: sqlite3.Connection, since_iso: str) -> list[dict[str, Any]]:
    """Reports written on/after `since_iso`, oldest first."""
    rows = conn.execute(
        "SELECT created_at, manner, report, questions FROM weekly_reports "
        "WHERE created_at >= ? ORDER BY created_at",
        (since_iso,),
    ).fetchall()
    return [_row(r) for r in rows]


def count_before(conn: sqlite3.Connection, since_iso: str) -> int:
    """How many reports fall outside (before) a window — so an export can say what it
    is leaving out instead of silently truncating."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM weekly_reports WHERE created_at < ?", (since_iso,)
    ).fetchone()
    return int(row["n"])
