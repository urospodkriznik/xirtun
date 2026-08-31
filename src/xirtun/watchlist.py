"""The nutrition watchlist: risks worth watching for THIS user, beyond their stated goals.

The weekly review is otherwise reactive — it comments on what the user explicitly asked
it to track (their notes and goals), which means everything they never thought to ask
about stays invisible. This is that blind spot's fix: the agent derives, from the whole
profile (diet style, medical conditions, family history, demographics, location,
supplements), the nutrients and risks that matter for this person regardless of what
they requested, and persists it here with the reasoning.

Persisted rather than re-derived each week for the same reason calibrated targets are
(see targets.py): a good derivation that lives only in one report message is lost the
moment it's sent. Persisting also enables ROTATION — `due_items` names the least
recently covered entries so each report goes deep on one or two instead of reciting the
whole list every week, which is what turns this kind of feature into ignorable noise.

Nothing here is user-specific: the code is generic, and each user's watchlist lives in
their own database, derived from their own profile.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from xirtun.storage import db

_KEY = "nutrition_watchlist"

# Keep the list focused: a sprawling watchlist can't be covered meaningfully on rotation.
MAX_ITEMS = 12
# How many items to review in depth per weekly report.
DUE_COUNT = 2


def read(conn: sqlite3.Connection) -> dict[str, Any]:
    raw = db.kv_get(conn, _KEY)
    return json.loads(raw) if raw else {}


def items(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return read(conn).get("items", [])


def write(
    conn: sqlite3.Connection,
    new_items: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> str:
    """Replace the watchlist. Each item needs a `name` and a `why` naming the profile
    evidence behind it. Existing items KEEP their last_reviewed date, so re-deriving the
    list doesn't reset the rotation and make everything look due again."""
    previous = {i["name"].strip().lower(): i.get("last_reviewed") for i in items(conn)}

    cleaned: list[dict[str, Any]] = []
    for item in new_items[:MAX_ITEMS]:
        name = str(item.get("name", "")).strip()
        why = str(item.get("why", "")).strip()
        if not name or not why:
            continue
        cleaned.append({
            "name": name,
            "why": why,
            "last_reviewed": previous.get(name.lower()),
        })

    if not cleaned:
        return "ERROR: no valid watchlist items — each needs a `name` and a `why`."

    now = now or datetime.now().astimezone()
    db.kv_set(conn, _KEY, json.dumps({"items": cleaned, "derived_at": now.isoformat()}))
    return f"Watchlist saved ({len(cleaned)} items): " + ", ".join(i["name"] for i in cleaned)


def mark_reviewed(
    conn: sqlite3.Connection, names: list[str], *, now: datetime | None = None,
) -> str:
    """Stamp the named items as covered, so rotation moves on to the others next week."""
    data = read(conn)
    current = data.get("items", [])
    if not current:
        return "ERROR: no watchlist to mark — save one with set_watchlist first."

    wanted = {n.strip().lower() for n in names if str(n).strip()}
    now = now or datetime.now().astimezone()
    marked = []
    for item in current:
        if item["name"].strip().lower() in wanted:
            item["last_reviewed"] = now.isoformat()
            marked.append(item["name"])

    if not marked:
        return f"ERROR: none of {list(names)} are on the watchlist."
    data["items"] = current
    db.kv_set(conn, _KEY, json.dumps(data))
    return "Marked reviewed: " + ", ".join(marked)


def _sort_key(item: dict[str, Any]) -> tuple[int, str]:
    # Never-reviewed items come first; then the oldest review date.
    last = item.get("last_reviewed")
    return (1, last) if last else (0, "")


def due_items(current: list[dict[str, Any]], count: int = DUE_COUNT) -> list[dict[str, Any]]:
    """The least recently covered items — what this week's report should go deep on."""
    return sorted(current, key=_sort_key)[:count]


def _age(last_reviewed: str | None, now: datetime) -> str:
    if not last_reviewed:
        return "never reviewed"
    days = (now.date() - datetime.fromisoformat(last_reviewed).date()).days
    return f"last reviewed {last_reviewed[:10]}, {days}d ago"


def format_watchlist(conn: sqlite3.Connection, now: datetime | None = None) -> str:
    now = now or datetime.now().astimezone()
    current = items(conn)
    if not current:
        return (
            "No watchlist yet. Derive one now with set_targets' sibling `set_watchlist`: "
            "read the user's FULL profile and list the nutrients and risks that matter "
            "for THIS person REGARDLESS of what they asked you to track — from their diet "
            "style, medical conditions, FAMILY HISTORY, age/sex, location, and the "
            "supplements they already take. This is the blind-spot list: things they "
            "would not know to ask about."
        )

    lines = [f"Nutrition watchlist (derived {read(conn).get('derived_at', '')[:10]}, from the "
             "user's profile — NOT their stated goals):"]
    for item in current:
        lines.append(f"- {item['name']} — {item['why']} ({_age(item.get('last_reviewed'), now)})")

    due = due_items(current)
    lines.append(
        "DUE for in-depth review this week: " + ", ".join(i["name"] for i in due) + ". "
        "Cover those in depth; mention the others only if this week's diary shows "
        "something notable about them. Call mark_watchlist_reviewed once you have."
    )
    return "\n".join(lines)
