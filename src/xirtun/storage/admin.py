"""Destructive data-management helpers (used by the /clear-data command)."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

# Fixed internal list (never user input) — safe to interpolate into the DELETE.
_USER_TABLES = ("meal_items", "meals", "symptoms", "pending", "runs")


def reset_all(conn: sqlite3.Connection, data_dir: Path) -> None:
    """Erase all user data: the diary tables and the markdown memory files.

    Leaves the `kv` table (the Telegram polling offset) intact so the bot keeps its
    place in the message stream after a wipe.
    """
    for table in _USER_TABLES:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()

    for name in ("diet.md", "observations.md"):
        (data_dir / name).unlink(missing_ok=True)
    history = data_dir / "diet.history"
    if history.exists():
        shutil.rmtree(history)
