"""Session tracking for the weekly review's calibrating questions.

Two modes, matching how the review was triggered:
- "interactive" (manual /weekly): the report is HELD until the questions are
  answered or skipped, then released.
- "capture" (scheduled run): the report already went out; replies just get filed
  as notes for next week — this week's analysis isn't redone.

Built on the generic `pending` session table (kind="weekly_qa", the JSON blob
below packed into Session.text) rather than a new table, matching how other
multi-turn flows (meal/symptom clarification) already work.

Waits for an explicit 'done'/'skip' rather than a short auto-timeout: a held report
must never be silently dropped just because the reply took a while. See TIMEOUT.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from xirtun.pipeline import sessions

KIND = "weekly_qa"
DONE_WORDS = {"done", "skip", "/done", "/skip"}

# Far longer than the default session TIMEOUT (30 min) — a held report or pending
# Q&A must survive the user being away for a day or two, not just a bathroom break.
TIMEOUT = timedelta(days=3)


@dataclass
class WeeklyQA:
    mode: str                              # "interactive" | "capture"
    questions: list[str]
    answers: list[str] = field(default_factory=list)
    report: str = ""                       # held report text; "" for "capture" mode


def _dump(qa: WeeklyQA) -> str:
    return json.dumps(
        {"mode": qa.mode, "questions": qa.questions, "answers": qa.answers, "report": qa.report}
    )


def _load(text: str) -> WeeklyQA:
    data = json.loads(text)
    return WeeklyQA(
        mode=data["mode"],
        questions=data.get("questions", []),
        answers=data.get("answers", []),
        report=data.get("report", ""),
    )


def start(
    conn: sqlite3.Connection, chat_id: str, *, mode: str, questions: list[str], report: str = "",
    now: datetime | None = None,
) -> None:
    qa = WeeklyQA(mode=mode, questions=questions, report=report)
    sessions.upsert(conn, chat_id, KIND, _dump(qa), now=now)


def get(conn: sqlite3.Connection, chat_id: str, *, now: datetime | None = None) -> WeeklyQA | None:
    session = sessions.get_active(conn, chat_id, now=now, timeout=TIMEOUT)
    if session is None or session.kind != KIND:
        return None
    return _load(session.text)


def persist(conn: sqlite3.Connection, chat_id: str, qa: WeeklyQA, *, now: datetime | None = None) -> None:
    """(Re-)write the session row from the given WeeklyQA — used to restore it if a
    log processed mid-flow opened its own competing session (the `pending` table
    holds one row per chat, so the two would otherwise clobber each other)."""
    sessions.upsert(conn, chat_id, KIND, _dump(qa), now=now)


def record_answer(
    conn: sqlite3.Connection, chat_id: str, qa: WeeklyQA, answer: str, *, now: datetime | None = None,
) -> None:
    qa.answers.append(answer)
    sessions.upsert(conn, chat_id, KIND, _dump(qa), now=now)


def clear(conn: sqlite3.Connection, chat_id: str) -> None:
    sessions.clear(conn, chat_id)


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
