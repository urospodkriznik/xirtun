"""Deterministic daily calorie/protein targets from the user's body metrics.

Metrics (sex, age, height, weight, activity) are captured at onboarding and stored as
JSON in the kv table; targets use the Mifflin–St Jeor equation. No LLM is involved —
a target shouldn't cost tokens or change between asks.

The formula is only a PRIOR, though: the weekly agent can persist a *calibrated*
working target (also in kv) justified by real evidence — weight trend, satiety
feedback, injury/activity changes. `set_calibrated` clamps to physiological bounds
so a bad LLM call can never store a dangerous number.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any

from xirtun.storage import db

_METRICS_KEY = "metrics"
_CALIBRATED_KEY = "calibrated_targets"
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
            "target below is only an ESTIMATE — ask the user to log /addweight regularly so "
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


def _bmr(metrics: dict[str, Any], today: date | None = None) -> float:
    """Mifflin–St Jeor basal metabolic rate. Assumes complete metrics."""
    sex = metrics["sex"]
    sex_constant = 5 if sex == "male" else (-161 if sex == "female" else -78)
    return (
        10 * metrics["weight_kg"]
        + 6.25 * metrics["height_cm"]
        - 5 * age_from(metrics, today)
        + sex_constant
    )


def compute(metrics: dict[str, Any], today: date | None = None) -> dict[str, int] | None:
    """Maintenance calories (Mifflin–St Jeor × activity) and a protein target, or
    None if any required metric is missing."""
    if not all(metrics.get(key) is not None for key in _REQUIRED):
        return None

    tdee = _bmr(metrics, today) * _ACTIVITY_FACTORS.get(metrics["activity"], 1.375)
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
            "activity). Re-run onboarding to set them, and use /addweight to keep your "
            "weight current."
        )
    protein = f"{targets['protein_min_g']}–{targets['protein_max_g']}g"
    return (
        f"Formula estimate: ~{targets['calories']} kcal/day, {protein} protein/day "
        f"(Mifflin–St Jeor from {metrics['weight_kg']}kg, {metrics['height_cm']}cm, "
        f"age {age_from(metrics)}, {metrics['activity']})."
    )


# --- calibrated working targets (set by the weekly agent, persisted in kv) ---

def read_calibrated(conn: sqlite3.Connection) -> dict[str, Any] | None:
    raw = db.kv_get(conn, _CALIBRATED_KEY)
    return json.loads(raw) if raw else None


def set_calibrated(
    conn: sqlite3.Connection,
    *,
    calories: int,
    protein_min_g: int,
    protein_max_g: int,
    rationale: str,
    now: datetime | None = None,
) -> str:
    """Persist a calibrated working target, clamped to physiological bounds so a bad
    LLM call can never store a dangerous number: calories within [BMR, 1.5×formula],
    protein within [0.8, 2.2] g/kg. Returns a confirmation naming any clamping."""
    metrics = read_metrics(conn)
    formula = compute(metrics)
    if formula is None:
        return "ERROR: can't calibrate targets — body metrics are incomplete."
    if not rationale.strip():
        return "ERROR: a rationale is required — say what evidence justifies the change."

    weight = metrics["weight_kg"]
    lo_cal, hi_cal = round(_bmr(metrics)), round(formula["calories"] * 1.5)
    lo_pro, hi_pro = round(0.8 * weight), round(2.2 * weight)

    clamped = []
    calories = int(calories)
    if not lo_cal <= calories <= hi_cal:
        calories = min(max(calories, lo_cal), hi_cal)
        clamped.append(f"calories clamped to {calories} (allowed {lo_cal}–{hi_cal})")
    protein_min_g = min(max(int(protein_min_g), lo_pro), hi_pro)
    protein_max_g = min(max(int(protein_max_g), lo_pro), hi_pro)
    if protein_min_g > protein_max_g:
        protein_min_g, protein_max_g = protein_max_g, protein_min_g

    now = now or datetime.now().astimezone()
    db.kv_set(conn, _CALIBRATED_KEY, json.dumps({
        "calories": calories,
        "protein_min_g": protein_min_g,
        "protein_max_g": protein_max_g,
        "rationale": rationale.strip(),
        "set_at": now.isoformat(),
    }))
    note = f" ({'; '.join(clamped)})" if clamped else ""
    return (
        f"Calibrated target saved: ~{calories} kcal/day, "
        f"{protein_min_g}–{protein_max_g}g protein/day{note}."
    )


def format_calibrated(conn: sqlite3.Connection) -> str:
    cal = read_calibrated(conn)
    if cal is None:
        return (
            "No calibrated target set yet — the formula estimate above is the working "
            "target. The weekly review adjusts it as evidence accumulates."
        )
    return (
        f"Current working target (calibrated {cal['set_at'][:10]}): "
        f"~{cal['calories']} kcal/day, {cal['protein_min_g']}–{cal['protein_max_g']}g "
        f"protein/day.\nWhy: {cal['rationale']}"
    )


def format_all_targets(conn: sqlite3.Connection) -> str:
    """Formula estimate + calibrated working target, for /target and the agent."""
    return f"{format_targets(read_metrics(conn))}\n{format_calibrated(conn)}"


def working_target(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """The number intake should be judged against: the calibrated target if one is
    set, otherwise the formula estimate. None if metrics are incomplete and nothing
    is calibrated. Includes a 'source' key ('calibrated' | 'formula')."""
    cal = read_calibrated(conn)
    if cal is not None:
        return {**cal, "source": "calibrated"}
    formula = compute(read_metrics(conn))
    return {**formula, "source": "formula"} if formula else None
