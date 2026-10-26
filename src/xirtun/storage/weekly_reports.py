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
from datetime import datetime, timedelta
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
    report["answers"] = json.loads(report["answers"]) if report.get("answers") else []
    return report


def record_answers(conn: sqlite3.Connection, answers: list[str]) -> None:
    """Attach the user's replies to the most recent report's questions. A Q&A always
    belongs to the latest report (it times out long before the next review), so no id
    needs threading through the conversation."""
    if not answers:
        return
    conn.execute(
        "UPDATE weekly_reports SET answers = ? WHERE id = (SELECT MAX(id) FROM weekly_reports)",
        (json.dumps(answers),),
    )
    conn.commit()


def since(conn: sqlite3.Connection, since_iso: str) -> list[dict[str, Any]]:
    """Reports written on/after `since_iso`, oldest first."""
    rows = conn.execute(
        "SELECT created_at, manner, report, questions, answers FROM weekly_reports "
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


# Cap on questions per review, and how far back a question counts as "already asked".
MAX_QUESTIONS = 2
REPEAT_WINDOW_DAYS = 28


def _tokens(text: str) -> set[str]:
    return {word.strip("?.,!:;'\"()").rstrip("s") for word in text.lower().split() if len(word) >= 4}


def _is_repeat(question: str, earlier: list[str]) -> bool:
    """Same question, possibly reworded: most of the shorter one's significant words
    already appear in an earlier one."""
    wanted = _tokens(question)
    if not wanted:
        return False
    for other in earlier:
        theirs = _tokens(other)
        if theirs and len(wanted & theirs) / min(len(wanted), len(theirs)) >= 0.5:
            return True
    return False


def drop_repeats(
    conn: sqlite3.Connection, questions: list[str], *, now: datetime,
) -> list[str]:
    """The questions worth sending: none that resemble one asked in the last
    REPEAT_WINDOW_DAYS (answered or skipped — a skipped one is a no on being asked),
    none that duplicate each other, and at most MAX_QUESTIONS. Done in code because the
    prompt alone asks for this and the agent still re-asked the same pair every week."""
    cutoff = (now - timedelta(days=REPEAT_WINDOW_DAYS)).isoformat()
    asked = [q for report in since(conn, cutoff) for q in report["questions"]]
    kept: list[str] = []
    for question in questions:
        if _is_repeat(question, asked + kept):
            continue
        kept.append(question)
    return kept[:MAX_QUESTIONS]


def format_history(conn: sqlite3.Connection, *, now: datetime, weeks: int = 6) -> str:
    """What the agent asked in recent reviews and what the user replied — the agent's
    only way to know a question has already been put."""
    reports = [r for r in since(conn, (now - timedelta(weeks=weeks)).isoformat()) if r["questions"]]
    if not reports:
        return f"No questions were asked in the last {weeks} weeks."
    lines = [f"Questions you asked in the last {weeks} weeks, and the replies:"]
    for r in reports:
        lines.append(f"- {r['created_at'][:10]}:")
        lines += [f"    Q: {q}" for q in r["questions"]]
        lines.append(
            "    Reply: " + " / ".join(r["answers"]) if r["answers"]
            else "    Reply: (none — skipped or unanswered)"
        )
    return "\n".join(lines)
