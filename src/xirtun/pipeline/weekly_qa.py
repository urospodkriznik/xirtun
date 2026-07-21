"""The weekly review's calibrating-questions Q&A, in its OWN `weekly_qa` table.

Two modes, matching how the review was triggered:
- "interactive" (manual /weekly): the report is HELD until the questions are
  answered or skipped, then released.
- "capture" (scheduled run): the report already went out; replies just get filed
  as notes for next week — this week's analysis isn't redone.

A dedicated table (not the shared `pending` slot) on purpose: this state is
long-lived (TIMEOUT below) and must survive unrelated commands — /undo, a meal
clarification, a bare-command prompt — that reuse the single per-chat `pending`
row. Sharing that row let any such command silently clobber a pending Q&A.

Waits for an explicit 'done'/'skip' rather than a short auto-timeout: a held report
must never be silently dropped just because the reply took a while. See TIMEOUT.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

DONE_WORDS = {"done", "skip", "/done", "/skip"}

# Long-lived: a held report or pending Q&A must survive the user being away for a
# day or two. Still bounded so an abandoned Q&A doesn't linger forever.
TIMEOUT = timedelta(days=3)


@dataclass
class WeeklyQA:
    mode: str                              # "interactive" | "capture"
    questions: list[str]
    answers: list[str] = field(default_factory=list)
    report: str = ""                       # held report text; "" for "capture" mode


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def start(
    conn: sqlite3.Connection, chat_id: str, *, mode: str, questions: list[str], report: str = "",
    now: datetime | None = None,
) -> None:
    conn.execute(
        "INSERT INTO weekly_qa (chat_id, mode, questions, answers, report, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(chat_id) DO UPDATE SET "
        "mode = excluded.mode, questions = excluded.questions, answers = excluded.answers, "
        "report = excluded.report, updated_at = excluded.updated_at",
        (chat_id, mode, json.dumps(questions), json.dumps([]), report, _now(now).isoformat()),
    )
    conn.commit()


def get(conn: sqlite3.Connection, chat_id: str, *, now: datetime | None = None) -> WeeklyQA | None:
    row = conn.execute(
        "SELECT mode, questions, answers, report, updated_at FROM weekly_qa WHERE chat_id = ?",
        (chat_id,),
    ).fetchone()
    if row is None:
        return None
    if datetime.fromisoformat(row["updated_at"]) < _now(now) - TIMEOUT:
        clear(conn, chat_id)
        return None
    return WeeklyQA(
        mode=row["mode"],
        questions=json.loads(row["questions"]),
        answers=json.loads(row["answers"]),
        report=row["report"],
    )


def _save(conn: sqlite3.Connection, chat_id: str, qa: WeeklyQA, now: datetime | None) -> None:
    conn.execute(
        "UPDATE weekly_qa SET mode = ?, questions = ?, answers = ?, report = ?, updated_at = ? "
        "WHERE chat_id = ?",
        (qa.mode, json.dumps(qa.questions), json.dumps(qa.answers), qa.report,
         _now(now).isoformat(), chat_id),
    )
    conn.commit()


def touch(conn: sqlite3.Connection, chat_id: str, qa: WeeklyQA, *, now: datetime | None = None) -> None:
    """Refresh updated_at (resets the TIMEOUT) without changing the Q&A — e.g. after a
    log is handled mid-flow, to keep the still-open questions alive while the user is
    actively interacting."""
    _save(conn, chat_id, qa, now)


def record_answer(
    conn: sqlite3.Connection, chat_id: str, qa: WeeklyQA, answer: str, *, now: datetime | None = None,
) -> None:
    qa.answers.append(answer)
    _save(conn, chat_id, qa, now)


def clear(conn: sqlite3.Connection, chat_id: str) -> None:
    conn.execute("DELETE FROM weekly_qa WHERE chat_id = ?", (chat_id,))
    conn.commit()


def is_done_word(text: str) -> bool:
    return text.strip().lower() in DONE_WORDS


def _numbered(questions: list[str]) -> str:
    return "\n".join(f"{i}. {q}" for i, q in enumerate(questions, start=1))


def format_intro(questions: list[str]) -> str:
    return (
        "Before I send this week's review, a couple of quick questions to sharpen it:\n"
        f"{_numbered(questions)}\n\n"
        "Reply with your answers (one message is fine — log meals in between if you need "
        "to, I'll come back to this), or send 'skip' to see the report now."
    )


def format_followup(questions: list[str]) -> str:
    return (
        "Quick follow-up from this week's review — your answers sharpen next week's:\n"
        f"{_numbered(questions)}\n\n"
        "Reply whenever, or send 'skip' to drop it."
    )


def format_reminder(questions: list[str]) -> str:
    return f"(Still waiting on this from before — reply or send 'skip'):\n{_numbered(questions)}"


def format_note(questions: list[str], answers: list[str]) -> str:
    """A note for next week's agent to read. Answers aren't paired 1:1 with questions
    — a user may answer several in one message or across several — so we hand over
    both blocks as-is and let the (LLM) reader correlate them, rather than force a
    fragile index alignment that could misattribute an answer to the wrong question."""
    if not answers:
        return f"Weekly Q&A: user skipped without answering. Questions asked:\n{_numbered(questions)}"
    return (
        f"Weekly Q&A follow-up — questions asked:\n{_numbered(questions)}\n\n"
        "User's reply (may not map 1:1 to the questions above):\n" + "\n".join(answers)
    )
