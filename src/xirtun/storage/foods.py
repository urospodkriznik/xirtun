"""The custom food database: per-100g nutrition for foods the user buys often.

When a logged item matches one of these by name, its macros are computed exactly
from the stored label values instead of being estimated by the model.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any


def add(conn: sqlite3.Connection, food: dict[str, Any], *, now: datetime | None = None) -> None:
    """Insert or update (by name) a known food. Values are per 100g."""
    now = now or datetime.now().astimezone()
    conn.execute(
        "INSERT INTO known_foods "
        "(name, brand, calories, protein_g, fat_g, carbs_g, sugar_g, fiber_g, package_g, tags, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(name) DO UPDATE SET "
        "brand = excluded.brand, calories = excluded.calories, "
        "protein_g = excluded.protein_g, fat_g = excluded.fat_g, "
        "carbs_g = excluded.carbs_g, sugar_g = excluded.sugar_g, fiber_g = excluded.fiber_g, "
        "package_g = excluded.package_g, tags = excluded.tags",
        (
            food["name"],
            food.get("brand"),
            food.get("calories"),
            food.get("protein_g"),
            food.get("fat_g"),
            food.get("carbs_g"),
            food.get("sugar_g"),
            food.get("fiber_g"),
            food.get("package_g"),
            json.dumps(food.get("tags", [])),
            now.isoformat(),
        ),
    )
    conn.commit()


def names(conn: sqlite3.Connection) -> list[str]:
    return [row["name"] for row in conn.execute("SELECT name FROM known_foods ORDER BY name")]


def all_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute("SELECT * FROM known_foods ORDER BY name")]


def for_prompt(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Name + package size for each known food, to list in the structuring prompt."""
    return [
        {"name": row["name"], "package_g": row["package_g"]}
        for row in conn.execute("SELECT name, package_g FROM known_foods ORDER BY name")
    ]


def find_by_name(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM known_foods WHERE lower(name) = lower(?)", (name,)
    ).fetchone()
    return dict(row) if row else None


def delete(conn: sqlite3.Connection, name: str) -> bool:
    """Remove a saved food by name (case-insensitive). Returns True if one was removed."""
    cursor = conn.execute("DELETE FROM known_foods WHERE lower(name) = lower(?)", (name,))
    conn.commit()
    return cursor.rowcount > 0


def _tokens(text: str) -> set[str]:
    # Significant words, with a crude plural strip so "falafel" matches "falafels".
    return {word.rstrip("s") for word in text.lower().split() if len(word) >= 4}


def search(conn: sqlite3.Connection, query: str) -> list[str]:
    """Saved food names that share a significant word with `query` (excluding an exact
    name match). Used for duplicate detection and /checkfood."""
    wanted = _tokens(query)
    matches = []
    for row in conn.execute("SELECT name FROM known_foods"):
        name = row["name"]
        if name.lower() == query.lower():
            continue
        if wanted & _tokens(name):
            matches.append(name)
    return matches
