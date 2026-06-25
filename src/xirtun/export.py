"""Diary export for the /export command.

SQLite is a binary file, so backing the diary up by hand means copying an opaque
blob. This dumps the whole diary — meals (with items), symptoms, and the custom
known-foods table — into one human-readable JSON document the user can keep.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from xirtun.storage import custom_meals, diary, foods

EXPORT_VERSION = 1


def build_export(conn: sqlite3.Connection, *, now: datetime | None = None) -> dict[str, Any]:
    """Assemble the full diary as a plain dict (everything the user has logged)."""
    now = now or datetime.now().astimezone()
    return {
        "version": EXPORT_VERSION,
        "exported_at": now.isoformat(),
        "meals": diary.all_meals(conn),
        "symptoms": diary.all_symptoms(conn),
        "exercises": diary.all_exercises(conn),
        "known_foods": foods.all_rows(conn),
        "custom_meals": custom_meals.all_rows(conn),
    }


def export_json(conn: sqlite3.Connection, *, now: datetime | None = None) -> str:
    """The diary export as pretty-printed JSON text."""
    return json.dumps(build_export(conn, now=now), indent=2, ensure_ascii=False)


def export_filename(now: datetime | None = None) -> str:
    """A timestamped filename, e.g. xirtun-export-20260624-1530.json."""
    now = now or datetime.now().astimezone()
    return f"xirtun-export-{now:%Y%m%d-%H%M}.json"
