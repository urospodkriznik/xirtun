"""Pending-meal session state, persisted in the `pending` table.

A session exists only while a meal is mid-clarification (or just opened via /addmeal).
It accumulates the user's description across messages so the structurer always sees
the full context. Sessions auto-expire after TIMEOUT so a forgotten meal doesn't
swallow your next, unrelated message.

The `now` parameter is injectable everywhere so tests can control the clock instead
of depending on the wall clock.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

TIMEOUT = timedelta(minutes=30)

# The onboarding interview is long and people step away in the middle of it — to look
# up a number, to eat, overnight. Forgetting the transcript after 30 minutes made the
# next answer restart the whole interview from scratch. A day is long enough to cover
# any ordinary pause; a forgotten interview still can't swallow messages for longer.
INTERVIEW_TIMEOUT = timedelta(hours=24)
_KIND_TIMEOUTS = {"onboarding": INTERVIEW_TIMEOUT, "onboarding_topup": INTERVIEW_TIMEOUT}


@dataclass
class Session:
    chat_id: str
    kind: str           # "meal" (later: "symptom")
    text: str           # accumulated description so far
    updated_at: datetime


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def peek_kind(conn: sqlite3.Connection, chat_id: str) -> str | None:
    """The kind of the pending session, if any, WITHOUT applying expiry — lets a
    caller pick the right timeout for get_active before it evaluates staleness
    (different session kinds may warrant very different timeouts)."""
    row = conn.execute("SELECT kind FROM pending WHERE chat_id = ?", (chat_id,)).fetchone()
    return row["kind"] if row else None


def get_active(
    conn: sqlite3.Connection, chat_id: str, *, now: datetime | None = None,
    timeout: timedelta | None = None,
) -> Session | None:
    """The live session, or None. `timeout` overrides how long it stays live; left
    out, it depends on the session's kind (INTERVIEW_TIMEOUT for onboarding, TIMEOUT
    for everything else)."""
    now = _now(now)
    row = conn.execute(
        "SELECT chat_id, kind, draft, updated_at FROM pending WHERE chat_id = ?",
        (chat_id,),
    ).fetchone()
    if row is None:
        return None

    updated_at = datetime.fromisoformat(row["updated_at"])
    if timeout is None:
        timeout = _KIND_TIMEOUTS.get(row["kind"], TIMEOUT)

    # Expire stale sessions so a forgotten meal doesn't capture a later, unrelated
    # message: drop anything older than `timeout` and treat it as "no active session".
    # Callers holding something that must never be silently lost (e.g. a withheld
    # weekly report awaiting Q&A) pass a much longer timeout than the default.
    if updated_at < now - timeout:
        clear(conn, chat_id)
        return None

    text = json.loads(row["draft"])["text"]
    return Session(chat_id=row["chat_id"], kind=row["kind"], text=text, updated_at=updated_at)


def upsert(conn: sqlite3.Connection, chat_id: str, kind: str, text: str, *, now: datetime | None = None) -> None:
    now = _now(now)
    conn.execute(
        "INSERT INTO pending (chat_id, kind, draft, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET "
        "kind = excluded.kind, draft = excluded.draft, updated_at = excluded.updated_at",
        (chat_id, kind, json.dumps({"text": text}), now.isoformat()),
    )
    conn.commit()


def clear(conn: sqlite3.Connection, chat_id: str) -> None:
    conn.execute("DELETE FROM pending WHERE chat_id = ?", (chat_id,))
    conn.commit()
