"""Deterministic daily calorie/protein targets from the user's body metrics.

Metrics (sex, age, height, weight, activity) are captured at onboarding and stored as
JSON in the kv table; targets use the Mifflin–St Jeor equation. No LLM is involved —
a target shouldn't cost tokens or change between asks.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
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


def read_metrics(conn: sqlite3.Connection) -> dict[str, Any]:
    raw = db.kv_get(conn, _METRICS_KEY)
    return json.loads(raw) if raw else {}


def write_metrics(conn: sqlite3.Connection, metrics: dict[str, Any]) -> None:
    db.kv_set(conn, _METRICS_KEY, json.dumps(metrics))


def update_weight(conn: sqlite3.Connection, weight_kg: float) -> None:
    metrics = read_metrics(conn)
    metrics["weight_kg"] = weight_kg
    write_metrics(conn, metrics)


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
    protein = 1.6 * metrics["weight_kg"]  # ~1.6 g/kg, a reasonable active baseline
    return {"calories": round(tdee), "protein_g": round(protein)}


def format_targets(metrics: dict[str, Any]) -> str:
    targets = compute(metrics)
    if targets is None:
        return (
            "I don't have your full metrics yet (sex, year of birth, height, weight, "
            "activity). Re-run onboarding to set them, and use /weight to keep your "
            "weight current."
        )
    return (
        f"Maintenance: ~{targets['calories']} kcal/day, ~{targets['protein_g']}g protein/day "
        f"(from {metrics['weight_kg']}kg, {metrics['height_cm']}cm, age {age_from(metrics)}, "
        f"{metrics['activity']}). Your weekly review tailors this to your goals."
    )
