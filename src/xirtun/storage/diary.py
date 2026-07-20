"""Persisting meals and their items. All meal/symptom SQL lives here; the rest of
the app calls these functions rather than writing SQL.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO-8601 string, or return None if missing/unparseable."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def save_meal(
    conn: sqlite3.Connection,
    raw_text: str,
    meal: dict[str, Any],
    *,
    now: datetime | None = None,
) -> int:
    """Insert a meal and its items. Returns the new meal id.

    `now` is injectable so tests can pin the timestamp instead of using the wall
    clock.
    """
    now = now or datetime.now().astimezone()
    # When the meal was eaten (model's estimate from the text), falling back to the
    # logging time when the text gave no time cue.
    occurred_at = _parse_dt(meal.get("occurred_at")) or now

    cursor = conn.execute(
        "INSERT INTO meals (occurred_at, logged_at, raw_text, notes) VALUES (?, ?, ?, ?)",
        (occurred_at.isoformat(), now.isoformat(), raw_text, meal.get("notes")),
    )
    meal_id = cursor.lastrowid

    for item in meal["items"]:
        conn.execute(
            "INSERT INTO meal_items (meal_id, name, quantity_g, calories, protein_g, fat_g, carbs_g, sugar_g, fiber_g, tags) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                meal_id,
                item["name"],
                item.get("quantity_g"),
                item.get("calories"),
                item.get("protein_g"),
                item.get("fat_g"),
                item.get("carbs_g"),
                item.get("sugar_g"),
                item.get("fiber_g"),
                json.dumps(item.get("tags", [])),
            ),
        )

    conn.commit()
    return meal_id


def save_symptom(
    conn: sqlite3.Connection,
    raw_text: str,
    symptom: dict[str, Any],
    *,
    now: datetime | None = None,
) -> int:
    """Insert one symptom event. Returns the new symptom id."""
    now = now or datetime.now().astimezone()
    occurred_at = _parse_dt(symptom.get("occurred_at")) or now

    cursor = conn.execute(
        "INSERT INTO symptoms (occurred_at, logged_at, type, severity, duration, raw_text, tags) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (occurred_at.isoformat(), now.isoformat(), symptom["type"], symptom.get("severity"), symptom.get("duration"), raw_text, json.dumps(symptom.get("tags", []))),
    )
    symptom_id = cursor.lastrowid
    conn.commit()
    return symptom_id


def save_exercise(
    conn: sqlite3.Connection,
    raw_text: str,
    exercise: dict[str, Any],
    *,
    now: datetime | None = None,
) -> int:
    """Insert one exercise event. Returns the new exercise id."""
    now = now or datetime.now().astimezone()
    occurred_at = _parse_dt(exercise.get("occurred_at")) or now

    cursor = conn.execute(
        "INSERT INTO exercises (occurred_at, logged_at, type, duration_min, intensity, "
        "calories_burned, distance_km, raw_text, notes, tags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            occurred_at.isoformat(),
            now.isoformat(),
            exercise["type"],
            exercise.get("duration_min"),
            exercise.get("intensity"),
            exercise.get("calories_burned"),
            exercise.get("distance_km"),
            raw_text,
            exercise.get("notes"),
            json.dumps(exercise.get("tags", [])),
        ),
    )
    conn.commit()
    return cursor.lastrowid


def meals_since(conn: sqlite3.Connection, since_iso: str) -> list[dict[str, Any]]:
    """Meals (with their items) eaten on/after `since_iso`, oldest first."""
    rows = conn.execute(
        "SELECT id, occurred_at FROM meals WHERE occurred_at >= ? ORDER BY occurred_at",
        (since_iso,),
    ).fetchall()
    result = []
    for r in rows:
        items = conn.execute(
            "SELECT name, calories, protein_g, fat_g, carbs_g, sugar_g, fiber_g, tags "
            "FROM meal_items WHERE meal_id = ?",
            (r["id"],),
        ).fetchall()
        result.append({"occurred_at": r["occurred_at"], "items": [dict(i) for i in items]})
    return result


def daily_totals(conn: sqlite3.Connection, since_iso: str) -> list[dict[str, Any]]:
    """Per-day intake totals (meal count, kcal, protein, fibre) on/after `since_iso`,
    oldest first. Computed in SQL so the weekly agent reads real arithmetic instead of
    summing dozens of items itself — LLMs are unreliable at exactly that."""
    rows = conn.execute(
        "SELECT date(m.occurred_at) AS day, COUNT(DISTINCT m.id) AS meals, "
        "COALESCE(SUM(i.calories), 0) AS calories, "
        "COALESCE(SUM(i.protein_g), 0) AS protein_g, "
        "COALESCE(SUM(i.fiber_g), 0) AS fiber_g "
        "FROM meals m JOIN meal_items i ON i.meal_id = m.id "
        "WHERE m.occurred_at >= ? GROUP BY day ORDER BY day",
        (since_iso,),
    ).fetchall()
    return [dict(r) for r in rows]


def weekly_totals(conn: sqlite3.Connection, now: datetime, *, weeks: int = 4) -> list[dict[str, Any]]:
    """Per-week intake aggregates for the last `weeks` weeks, most recent first.

    Week 0 is the trailing 7 days (now-7d .. now), week 1 the 7 days before that, etc.
    Averages are over days that actually have logged meals, so a sparsely logged week
    isn't dragged toward zero — `days_logged` is returned alongside so the caller can
    judge how complete each week was. Bucketing is done here (not the LLM) so
    week-over-week deltas rest on real arithmetic."""
    since = (now - timedelta(days=weeks * 7)).isoformat()
    buckets: dict[int, dict[str, float]] = {}
    for row in daily_totals(conn, since):
        idx = (now.date() - date.fromisoformat(row["day"])).days // 7
        if not 0 <= idx < weeks:
            continue
        b = buckets.setdefault(idx, {"days_logged": 0, "calories": 0.0, "protein_g": 0.0, "fiber_g": 0.0})
        b["days_logged"] += 1
        b["calories"] += row["calories"]
        b["protein_g"] += row["protein_g"]
        b["fiber_g"] += row["fiber_g"]

    result = []
    for idx in range(weeks):
        b = buckets.get(idx)
        d = b["days_logged"] if b else 0
        result.append({
            "weeks_ago": idx,
            "days_logged": d,
            "avg_calories": (b["calories"] / d) if d else 0.0,
            "avg_protein_g": (b["protein_g"] / d) if d else 0.0,
            "avg_fiber_g": (b["fiber_g"] / d) if d else 0.0,
        })
    return result


def late_meal_days(conn: sqlite3.Connection, since_iso: str, *, hour: int = 20) -> list[str]:
    """Datetimes ('YYYY-MM-DD HH:MM') of meals eaten at/after `hour` o'clock,
    on/after `since_iso` — the reflux-window check, computed not inferred."""
    rows = conn.execute(
        "SELECT occurred_at FROM meals "
        "WHERE occurred_at >= ? AND CAST(strftime('%H', occurred_at) AS INTEGER) >= ? "
        "ORDER BY occurred_at",
        (since_iso, hour),
    ).fetchall()
    return [r["occurred_at"][:16].replace("T", " ") for r in rows]


def symptoms_since(conn: sqlite3.Connection, since_iso: str) -> list[dict[str, Any]]:
    """Symptom events on/after `since_iso`, oldest first."""
    rows = conn.execute(
        "SELECT occurred_at, type, severity, duration, tags "
        "FROM symptoms WHERE occurred_at >= ? ORDER BY occurred_at",
        (since_iso,),
    ).fetchall()
    return [dict(r) for r in rows]


def exercises_since(conn: sqlite3.Connection, since_iso: str) -> list[dict[str, Any]]:
    """Exercise events on/after `since_iso`, oldest first."""
    rows = conn.execute(
        "SELECT occurred_at, type, duration_min, intensity, calories_burned, distance_km, tags "
        "FROM exercises WHERE occurred_at >= ? ORDER BY occurred_at",
        (since_iso,),
    ).fetchall()
    return [dict(r) for r in rows]


def all_meals(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every meal (with its items), oldest first — full fidelity, for /export."""
    rows = conn.execute(
        "SELECT id, occurred_at, logged_at, raw_text, notes FROM meals ORDER BY occurred_at, id"
    ).fetchall()
    result = []
    for r in rows:
        items = conn.execute(
            "SELECT name, quantity_g, calories, protein_g, fat_g, carbs_g, sugar_g, fiber_g, tags "
            "FROM meal_items WHERE meal_id = ? ORDER BY id",
            (r["id"],),
        ).fetchall()
        meal = {k: r[k] for k in ("occurred_at", "logged_at", "raw_text", "notes")}
        meal["items"] = [_item_with_tags(i) for i in items]
        result.append(meal)
    return result


def all_symptoms(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every symptom event, oldest first — full fidelity, for /export."""
    rows = conn.execute(
        "SELECT occurred_at, logged_at, type, severity, duration, raw_text, tags "
        "FROM symptoms ORDER BY occurred_at, id"
    ).fetchall()
    return [_item_with_tags(r) for r in rows]


def all_exercises(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every exercise event, oldest first — full fidelity, for /export."""
    rows = conn.execute(
        "SELECT occurred_at, logged_at, type, duration_min, intensity, calories_burned, "
        "distance_km, raw_text, notes, tags FROM exercises ORDER BY occurred_at, id"
    ).fetchall()
    return [_item_with_tags(r) for r in rows]


def _item_with_tags(row: sqlite3.Row) -> dict[str, Any]:
    """Row -> dict with the JSON `tags` column decoded back into a list."""
    item = dict(row)
    item["tags"] = json.loads(item["tags"]) if item.get("tags") else []
    return item


def last_entry(
    conn: sqlite3.Connection,
    *,
    extra_candidates: list[tuple[str, datetime | None, Any, str]] = (),
) -> dict[str, Any] | None:
    """The most recent entry the user created — a logged meal/symptom/exercise, or a
    saved food/meal — without deleting it. Saved items compare by created_at so /undo
    can take back a /savefood or /savemeal the same way it takes back a logged meal.

    `extra_candidates` lets a caller fold in entries that don't live in this database
    (e.g. a note in diet.md) so recency is compared across both — each is
    (kind, occurred_at, id, description); entries with no timestamp are ignored.
    """
    meal = conn.execute(
        "SELECT id, logged_at, raw_text FROM meals ORDER BY logged_at DESC, id DESC LIMIT 1"
    ).fetchone()
    symptom = conn.execute(
        "SELECT id, logged_at, type FROM symptoms ORDER BY logged_at DESC, id DESC LIMIT 1"
    ).fetchone()
    exercise = conn.execute(
        "SELECT id, logged_at, type FROM exercises ORDER BY logged_at DESC, id DESC LIMIT 1"
    ).fetchone()
    food = conn.execute(
        "SELECT id, created_at, name FROM known_foods ORDER BY created_at DESC, id DESC LIMIT 1"
    ).fetchone()
    saved_meal = conn.execute(
        "SELECT id, created_at, name FROM custom_meals ORDER BY created_at DESC, id DESC LIMIT 1"
    ).fetchone()

    candidates = []
    if meal is not None:
        candidates.append(("meal", _parse_dt(meal["logged_at"]), meal["id"], f"meal: {meal['raw_text']}"))
    if symptom is not None:
        candidates.append(("symptom", _parse_dt(symptom["logged_at"]), symptom["id"], f"symptom: {symptom['type']}"))
    if exercise is not None:
        candidates.append(("exercise", _parse_dt(exercise["logged_at"]), exercise["id"], f"exercise: {exercise['type']}"))
    if food is not None:
        candidates.append(("food", _parse_dt(food["created_at"]), food["id"], f"saved food: {food['name']}"))
    if saved_meal is not None:
        candidates.append(("saved_meal", _parse_dt(saved_meal["created_at"]), saved_meal["id"], f"saved meal: {saved_meal['name']}"))
    candidates += [c for c in extra_candidates if c[1] is not None]
    if not candidates:
        return None

    kind, _, entry_id, description = max(candidates, key=lambda c: c[1])
    return {"kind": kind, "id": entry_id, "description": description}


_TABLES = {
    "meal": "meals",
    "symptom": "symptoms",
    "exercise": "exercises",
    "food": "known_foods",
    "saved_meal": "custom_meals",
}


def delete_entry(conn: sqlite3.Connection, kind: str, entry_id: int) -> None:
    # `kind` comes from last_entry (never user input) — safe to choose the table.
    conn.execute(f"DELETE FROM {_TABLES[kind]} WHERE id = ?", (entry_id,))  # meal_items cascade
    conn.commit()


def delete_last(conn: sqlite3.Connection) -> str | None:
    """Delete the most recently logged entry. Returns its description, or None."""
    entry = last_entry(conn)
    if entry is None:
        return None
    delete_entry(conn, entry["kind"], entry["id"])
    return entry["description"]


def recent_meals(conn: sqlite3.Connection, limit: int = 3) -> list[dict[str, Any]]:
    """The most recent meals (with item names + calories), newest first."""
    rows = conn.execute(
        "SELECT id, occurred_at FROM meals ORDER BY occurred_at DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()
    result = []
    for r in rows:
        items = conn.execute(
            "SELECT name, calories FROM meal_items WHERE meal_id = ?", (r["id"],)
        ).fetchall()
        result.append({"occurred_at": r["occurred_at"], "items": [dict(i) for i in items]})
    return result


def recent_symptoms(conn: sqlite3.Connection, limit: int = 3) -> list[dict[str, Any]]:
    """The most recent symptom events, newest first."""
    rows = conn.execute(
        "SELECT occurred_at, type, severity, duration FROM symptoms "
        "ORDER BY occurred_at DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def recent_exercises(conn: sqlite3.Connection, limit: int = 3) -> list[dict[str, Any]]:
    """The most recent exercise events, newest first."""
    rows = conn.execute(
        "SELECT occurred_at, type, duration_min, intensity, calories_burned FROM exercises "
        "ORDER BY occurred_at DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
