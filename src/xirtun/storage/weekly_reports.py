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
    findings: list[str] | None = None,
    now: datetime | None = None,
) -> int:
    """Store one weekly report. Returns its new id."""
    now = now or datetime.now().astimezone()
    cursor = conn.execute(
        "INSERT INTO weekly_reports (created_at, manner, report, questions, findings) "
        "VALUES (?, ?, ?, ?, ?)",
        (now.isoformat(), manner, report, json.dumps(questions or []), json.dumps(findings or [])),
    )
    conn.commit()
    return cursor.lastrowid


def _row(row: sqlite3.Row) -> dict[str, Any]:
    report = dict(row)
    report["questions"] = json.loads(report["questions"]) if report.get("questions") else []
    report["answers"] = json.loads(report["answers"]) if report.get("answers") else []
    report["findings"] = json.loads(report["findings"]) if report.get("findings") else []
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
        "SELECT created_at, manner, report, questions, answers, findings FROM weekly_reports "
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
    """Significant words, normalised enough that a reworded sentence still matches.
    Hyphens and slashes split like spaces, so "late-evening eating" and "late evening
    eating" produce the same tokens rather than reading as unrelated phrases."""
    flattened = text.lower().replace("-", " ").replace("/", " ")
    return {w.strip("?.,!:;'\"()").rstrip("s") for w in flattened.split() if len(w) >= 4}


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


# A finding seen this many weeks running, unchanged, has stopped being news.
STALE_AFTER_WEEKS = 3
# How far back to look when counting how often a finding has been raised.
FINDING_WINDOW_WEEKS = 12


def finding_history(
    conn: sqlite3.Connection, *, now: datetime, weeks: int = FINDING_WINDOW_WEEKS,
) -> list[dict[str, Any]]:
    """How often each past finding has been raised, newest label first.

    Findings are free text, so the same concern comes back slightly reworded every week
    ("30% of energy after 20:00", "late-evening eating every night"). They are grouped
    by the same token-overlap test that catches reworded questions, so the count
    reflects the concern rather than the phrasing. Returned per group: the most recent
    label, how many reports raised it, and when it was first and last raised.
    """
    reports = since(conn, (now - timedelta(weeks=weeks)).isoformat())
    groups: list[dict[str, Any]] = []
    for report in reports:
        for label in report["findings"]:
            match = next((g for g in groups if _is_repeat(label, g["labels"])), None)
            if match is None:
                groups.append({
                    "label": label, "labels": [label], "count": 1,
                    "first": report["created_at"][:10], "last": report["created_at"][:10],
                })
            else:
                match["labels"].append(label)
                match.update(label=label, count=match["count"] + 1, last=report["created_at"][:10])
    groups.sort(key=lambda g: (-g["count"], g["label"]))
    return groups


def format_finding_history(
    conn: sqlite3.Connection, *, now: datetime, weeks: int = FINDING_WINDOW_WEEKS,
) -> str:
    """What the agent has already told this user, and how many times.

    Exists because the agent had no way to know: every week it rediscovered late-night
    eating and processed evening food and wrote them up afresh, which is accurate and
    useless. Counting is done here rather than asked for in the prompt for the same
    reason question de-duplication is (see drop_repeats).
    """
    groups = finding_history(conn, now=now, weeks=weeks)
    if not groups:
        return (
            f"No findings recorded in the last {weeks} weeks — nothing of yours is on "
            "record as already said, so write the report normally. Do set `findings` "
            "when you finish so next week knows what you covered."
        )

    lines = [f"What you have already told this user (last {weeks} weeks):"]
    for g in groups:
        span = g["first"] if g["first"] == g["last"] else f"{g['first']} → {g['last']}"
        tag = " — STALE, they have heard this" if g["count"] >= STALE_AFTER_WEEKS else ""
        lines.append(f"- \"{g['label']}\" — raised {g['count']}x ({span}){tag}")
    lines.append(
        f"A finding raised {STALE_AFTER_WEEKS}x or more is not news. If it is unchanged, "
        "give it ONE short line under 'Unchanged' — do not re-explain the mechanism, do "
        "not repeat the same swaps, and do not re-issue it as an action. Spend the report "
        "on what is NEW, has CHANGED, or has REGRESSED. If a stale finding has an action "
        "the user has plainly not taken for weeks, stop re-issuing it and consider asking "
        "once whether they want to work on it or want you to drop it."
    )
    return "\n".join(lines)
