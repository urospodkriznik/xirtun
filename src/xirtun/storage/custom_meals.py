"""Saved custom meals (recurring recipes): a named set of structured meal items.

Logging by name expands to the stored items, so a recurring breakfast is one phrase
instead of re-listing every ingredient each time.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

_MACROS = ("calories", "protein_g", "fat_g", "carbs_g")


def totals(items: list[dict[str, Any]]) -> dict[str, float]:
    out = {macro: 0.0 for macro in _MACROS}
    for item in items:
        for macro in _MACROS:
            out[macro] += item.get(macro) or 0
    return out


def add(conn: sqlite3.Connection, name: str, items: list[dict[str, Any]], *, now: datetime | None = None) -> None:
    """Insert or update (by name) a custom meal from its structured items."""
    now = now or datetime.now().astimezone()
    t = totals(items)
    conn.execute(
        "INSERT INTO custom_meals (name, items, calories, protein_g, fat_g, carbs_g, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(name) DO UPDATE SET items = excluded.items, calories = excluded.calories, "
        "protein_g = excluded.protein_g, fat_g = excluded.fat_g, carbs_g = excluded.carbs_g",
        (name, json.dumps(items), t["calories"], t["protein_g"], t["fat_g"], t["carbs_g"], now.isoformat()),
    )
    conn.commit()


def find_by_name(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM custom_meals WHERE lower(name) = lower(?)", (name,)
    ).fetchone()
    if row is None:
        return None
    meal = dict(row)
    meal["items"] = json.loads(meal["items"])
    return meal


def names(conn: sqlite3.Connection) -> list[str]:
    return [row["name"] for row in conn.execute("SELECT name FROM custom_meals ORDER BY name")]


def all_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [find_by_name(conn, row["name"]) for row in conn.execute("SELECT name FROM custom_meals ORDER BY name")]


def delete(conn: sqlite3.Connection, name: str) -> bool:
    cursor = conn.execute("DELETE FROM custom_meals WHERE lower(name) = lower(?)", (name,))
    conn.commit()
    return cursor.rowcount > 0
