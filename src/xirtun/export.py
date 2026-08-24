"""Diary backup for the /exportbackup command.

SQLite is a binary file, so backing the diary up by hand means copying an opaque
blob. This dumps everything the app knows into one human-readable JSON document:
not just what was logged, but who it was logged by — body metrics, targets, the
agent-managed profile and memory. A backup that restores your meals but not your
metrics isn't a backup; it's half of one.

The companion command, /exportdeepdive (`deepdive.py`), answers a different
question: it writes a Markdown briefing for a large model to *read*. This file
stays machine-shaped and versioned so a future importer can consume old exports.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from xirtun import targets
from xirtun.memory import diet as memory_diet
from xirtun.memory import observations as memory_observations
from xirtun.storage import custom_meals, db, diary, foods, weekly_reports

# 1: meals, symptoms, exercises, known_foods, custom_meals.
# 2: + metrics, targets, weight log, timezone, weekly reports, and the memory files
#    (diet.md, its history snapshots, observations.md) — everything needed to stand
#    the app back up, not just the diary.
EXPORT_VERSION = 2


def _memory_files(diet_path: Path | None, observations_path: Path | None) -> dict[str, Any]:
    """The markdown memory as plain text, including diet.md's history snapshots —
    those exist precisely because an agent rewrite can lose something, which makes
    them worth backing up too."""
    history = []
    if diet_path is not None:
        history_dir = diet_path.parent / "diet.history"
        if history_dir.is_dir():
            history = [
                {"filename": p.name, "content": p.read_text(encoding="utf-8")}
                for p in sorted(history_dir.glob("*.md"))
            ]
    return {
        "diet_md": memory_diet.read_diet(diet_path) if diet_path else "",
        "diet_history": history,
        "observations_md": memory_observations.read(observations_path) if observations_path else "",
    }


def build_export(
    conn: sqlite3.Connection,
    *,
    diet_path: Path | None = None,
    observations_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the full backup as a plain dict."""
    now = now or datetime.now().astimezone()
    metrics = targets.read_metrics(conn)
    return {
        "version": EXPORT_VERSION,
        "exported_at": now.isoformat(),
        # --- who this is (restoring without these means re-running onboarding) ---
        "metrics": metrics,
        "timezone": db.kv_get(conn, "timezone"),
        "onboarding_version": db.kv_get(conn, "onboarding_version"),
        # --- what intake is judged against ---
        "targets": {
            "formula": targets.compute(metrics),
            "calibrated": targets.read_calibrated(conn),
        },
        "weight_log": targets.weight_history(conn, "0000-01-01"),
        # --- the diary itself ---
        "meals": diary.all_meals(conn),
        "symptoms": diary.all_symptoms(conn),
        "exercises": diary.all_exercises(conn),
        "known_foods": foods.all_rows(conn),
        "custom_meals": custom_meals.all_rows(conn),
        # --- what the agent wrote ---
        "weekly_reports": weekly_reports.since(conn, "0000-01-01"),
        "memory": _memory_files(diet_path, observations_path),
    }


def export_json(
    conn: sqlite3.Connection,
    *,
    diet_path: Path | None = None,
    observations_path: Path | None = None,
    now: datetime | None = None,
) -> str:
    """The backup as pretty-printed JSON text."""
    return json.dumps(
        build_export(conn, diet_path=diet_path, observations_path=observations_path, now=now),
        indent=2,
        ensure_ascii=False,
    )


def export_filename(now: datetime | None = None) -> str:
    """A timestamped filename, e.g. xirtun-export-20260624-1530.json."""
    now = now or datetime.now().astimezone()
    return f"xirtun-export-{now:%Y%m%d-%H%M}.json"
