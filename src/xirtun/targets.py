"""Deterministic daily calorie/protein targets from the user's body metrics.

Metrics (sex, age, height, weight, activity) are captured at onboarding and stored as
JSON in the kv table; targets use the Mifflin–St Jeor equation. No LLM is involved —
a target shouldn't cost tokens or change between asks.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any

from xirtun.storage import db

_METRICS_KEY = "metrics"
_REQUIRED = ("sex", "birth_year", "height_cm", "weight_kg", "activity")
_ACTIVITY_FACTORS = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "active": 1.725,
    "very_active": 1.9,
}
# Protein range (min g/kg, max g/kg) per activity level.
# Grounded in WHO + ACSM guidelines: higher training load → higher protein need.
_PROTEIN_RANGE = {
    "sedentary":  (1.0, 1.2),
    "light":      (1.2, 1.4),
    "moderate":   (1.4, 1.6),
    "active":     (1.6, 1.8),
    "very_active":(1.8, 2.0),
}


def read_metrics(conn: sqlite3.Connection) -> dict[str, Any]:
    raw = db.kv_get(conn, _METRICS_KEY)
    return json.loads(raw) if raw else {}


def write_metrics(conn: sqlite3.Connection, metrics: dict[str, Any]) -> None:
    db.kv_set(conn, _METRICS_KEY, json.dumps(metrics))


def update_weight(conn: sqlite3.Connection, weight_kg: float, now: datetime | None = None) -> None:
    """Set the current weight AND append a dated entry to weight_log, so the weekly
    review can read the trend (the real arbiter of whether calorie targets are right)."""
    metrics = read_metrics(conn)
    metrics["weight_kg"] = weight_kg
    write_metrics(conn, metrics)
    occurred_at = (now or datetime.now()).isoformat()
    conn.execute(
        "INSERT INTO weight_log (occurred_at, weight_kg) VALUES (?, ?)",
        (occurred_at, weight_kg),
    )
    conn.commit()


def weight_history(conn: sqlite3.Connection, since_iso: str) -> list[dict[str, Any]]:
    """Logged weights at or after ``since_iso``, oldest first."""
    rows = conn.execute(
        "SELECT occurred_at, weight_kg FROM weight_log WHERE occurred_at >= ? "
        "ORDER BY occurred_at ASC",
        (since_iso,),
    ).fetchall()
    return [dict(r) for r in rows]


def format_weight_trend(conn: sqlite3.Connection, now: datetime | None = None, days: int = 56) -> str:
    """Human-readable weight trend over the last ``days`` for the weekly agent: the
    first and latest logged weight, net change, and approximate weekly rate."""
    now = now or datetime.now()
    since = (now - timedelta(days=days)).isoformat()
    history = weight_history(conn, since)
    if not history:
        return (
            f"No weights logged in the last {days} days. Without a trend, the calorie "
            "target below is only an ESTIMATE — ask the user to log /weight regularly so "
            "intake can be judged against reality, not the formula."
        )
    if len(history) == 1:
        h = history[0]
        return f"Only one weight logged ({h['weight_kg']:g}kg on {h['occurred_at'][:10]}). Not enough for a trend yet."

    first, last = history[0], history[-1]
    delta = last["weight_kg"] - first["weight_kg"]
    span_days = max(1, (datetime.fromisoformat(last["occurred_at"]) - datetime.fromisoformat(first["occurred_at"])).days)
    per_week = delta / span_days * 7
    direction = "down" if delta < 0 else ("up" if delta > 0 else "flat")
    return (
        f"Weight trend ({len(history)} entries over {span_days}d): "
        f"{first['weight_kg']:g}kg → {last['weight_kg']:g}kg "
        f"({delta:+.1f}kg, ~{per_week:+.2f}kg/week, {direction}). "
        "Treat this trend — not the formula — as the truth about whether intake is too high or too low."
    )


def age_from(metrics: dict[str, Any], today: date | None = None) -> int | None:
    """Current age from year (and optional month) of birth."""
    birth_year = metrics.get("birth_year")
    if birth_year is None:
        return None
    today = today or date.today()
    birth_month = metrics.get("birth_month") or 1
    had_birthday_this_year = today.month >= birth_month
    return today.year - birth_year - (0 if had_birthday_this_year else 1)


def compute(metrics: dict[str, Any], today: date | None = None) -> dict[str, int] | None:
    """Maintenance calories (Mifflin–St Jeor × activity) and a protein target, or
    None if any required metric is missing."""
    if not all(metrics.get(key) is not None for key in _REQUIRED):
        return None

    sex = metrics["sex"]
    sex_constant = 5 if sex == "male" else (-161 if sex == "female" else -78)
    bmr = (
        10 * metrics["weight_kg"]
        + 6.25 * metrics["height_cm"]
        - 5 * age_from(metrics, today)
        + sex_constant
    )
    tdee = bmr * _ACTIVITY_FACTORS.get(metrics["activity"], 1.375)
    lo, hi = _PROTEIN_RANGE.get(metrics["activity"], (1.4, 1.6))
    return {
        "calories": round(tdee),
        "protein_min_g": round(lo * metrics["weight_kg"]),
        "protein_max_g": round(hi * metrics["weight_kg"]),
    }


def format_targets(metrics: dict[str, Any]) -> str:
    targets = compute(metrics)
    if targets is None:
        return (
            "I don't have your full metrics yet (sex, year of birth, height, weight, "
            "activity). Re-run onboarding to set them, and use /weight to keep your "
            "weight current."
        )
    protein = f"{targets['protein_min_g']}–{targets['protein_max_g']}g"
    return (
        f"Maintenance: ~{targets['calories']} kcal/day, {protein} protein/day "
        f"(from {metrics['weight_kg']}kg, {metrics['height_cm']}cm, age {age_from(metrics)}, "
        f"{metrics['activity']}). Your weekly review tailors this to your goals."
    )
