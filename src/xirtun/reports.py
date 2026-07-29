"""Deterministic diary reports for the /today and /week commands (no LLM involved)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from xirtun import targets
from xirtun.memory import diet as memory_diet
from xirtun.storage import diary

_MACROS = ("calories", "protein_g", "fat_g", "carbs_g", "sugar_g", "fiber_g")


def _totals(meals: list[dict[str, Any]]) -> dict[str, float]:
    totals = {key: 0.0 for key in _MACROS}
    for meal in meals:
        for item in meal["items"]:
            for key in _MACROS:
                totals[key] += item.get(key) or 0
    return totals


def today_report(conn: sqlite3.Connection, now: datetime) -> str:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    meals = diary.meals_since(conn, start.isoformat())
    if not meals:
        return "No meals logged today yet."

    t = _totals(meals)
    lines = [
        f"Today — {len(meals)} meal(s), ~{round(t['calories'])} kcal "
        f"({round(t['protein_g'])}g protein, {round(t['fat_g'])}g fat, "
        f"{round(t['carbs_g'])}g carbs incl. {round(t['sugar_g'])}g sugar, "
        f"{round(t['fiber_g'])}g fibre):"
    ]
    for meal in meals:
        names = ", ".join(item["name"] for item in meal["items"])
        lines.append(f"- {names}")
    return "\n".join(lines)


def _amount(value: float, unit: str) -> str:
    """'120g' / '1200 kcal' — grams hug the number, kcal doesn't."""
    return f"{round(value)}g" if unit == "g" else f"{round(value)} {unit}"


def _range_line(label: str, eaten: float, lo: float, hi: float, unit: str = "g") -> str:
    """Eaten vs a target band, e.g. 'Protein: 60g of 112–128g → 52–68g left'."""
    band = f"{round(lo)}–{_amount(hi, unit)}"
    if eaten > hi:
        return f"- {label}: {_amount(eaten, unit)} of {band} → {_amount(eaten - hi, unit)} over"
    left_lo = max(lo - eaten, 0)
    return (
        f"- {label}: {_amount(eaten, unit)} of {band} "
        f"→ {round(left_lo)}–{_amount(hi - eaten, unit)} left"
    )


def _point_line(label: str, eaten: float, target: float, unit: str) -> str:
    """Eaten vs a single number, e.g. 'Calories: 1400 of ~2500 kcal → 1100 kcal left'."""
    remaining = target - eaten
    verdict = f"{_amount(remaining, unit)} left" if remaining >= 0 else f"{_amount(-remaining, unit)} over"
    return f"- {label}: {round(eaten)} of ~{_amount(target, unit)} → {verdict}"


def _cap_line(label: str, eaten: float, cap: float) -> str:
    """Eaten vs a ceiling (sugar) — the goal is to stay under it, not to reach it."""
    if eaten > cap:
        return f"- {label}: {_amount(eaten, 'g')} of ≤{_amount(cap, 'g')} → {_amount(eaten - cap, 'g')} over the cap"
    return f"- {label}: {_amount(eaten, 'g')} of ≤{_amount(cap, 'g')} → {_amount(cap - eaten, 'g')} before the cap"


def _floor_line(label: str, eaten: float, floor: float) -> str:
    """Eaten vs a minimum (fibre) — the goal is to reach it, with no upper bound."""
    if eaten >= floor:
        return f"- {label}: {_amount(eaten, 'g')} of ≥{_amount(floor, 'g')} → target met"
    return f"- {label}: {_amount(eaten, 'g')} of ≥{_amount(floor, 'g')} → {_amount(floor - eaten, 'g')} to go"


def remaining_today_report(conn: sqlite3.Connection, now: datetime) -> str:
    """What's still left of today's target, nutrient by nutrient.

    Calories and protein come from the working target (calibrated if the weekly
    review has set one, formula otherwise); fat, carbs, sugar and fibre come from
    the guideline split of that calorie target, and are labelled as such — they are
    not personalised the way calories and protein are.
    """
    target = targets.working_target(conn)
    if target is None:
        return (
            "I can't work out what's left today — I don't have your full body metrics "
            "and no target has been calibrated yet."
        )

    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    meals = diary.meals_since(conn, start.isoformat())
    t = _totals(meals)
    g = targets.macro_guidelines(target["calories"])

    header = f"Still to eat today (vs the {target['source']} target)"
    if not meals:
        header += " — nothing logged yet, so this is the whole day"
    return "\n".join([
        header + ":",
        _point_line("Calories", t["calories"], target["calories"], "kcal"),
        _range_line("Protein", t["protein_g"], target["protein_min_g"], target["protein_max_g"]),
        _range_line("Fat", t["fat_g"], g["fat_min_g"], g["fat_max_g"]),
        _range_line("Carbs", t["carbs_g"], g["carbs_min_g"], g["carbs_max_g"]),
        _cap_line("Sugar", t["sugar_g"], g["sugar_max_g"]),
        _floor_line("Fibre", t["fiber_g"], g["fiber_min_g"]),
        "(Fat, carbs, sugar and fibre are general guidelines derived from your "
        "calorie target — only calories and protein are calibrated for you.)",
    ])


def week_report(conn: sqlite3.Connection, now: datetime) -> str:
    start = now - timedelta(days=7)
    meals = diary.meals_since(conn, start.isoformat())
    symptoms = diary.symptoms_since(conn, start.isoformat())
    exercises = diary.exercises_since(conn, start.isoformat())
    if not meals and not symptoms and not exercises:
        return "Nothing logged in the past 7 days."

    t = _totals(meals)
    # Average over days that actually have entries (the date part of occurred_at),
    # not a flat 7 — otherwise sparse logging looks misleadingly low.
    days_logged = len({meal["occurred_at"][:10] for meal in meals}) or 1
    return (
        f"Past 7 days — {len(meals)} meals across {days_logged} day(s), "
        f"~{round(t['calories'])} kcal total "
        f"(~{round(t['calories'] / days_logged)}/day on logged days), "
        f"{round(t['protein_g'])}g protein total "
        f"(~{round(t['protein_g'] / days_logged)}/day), "
        f"{round(t['fiber_g'])}g fibre total "
        f"(~{round(t['fiber_g'] / days_logged)}/day). "
        f"Exercise: {len(exercises)} session(s), "
        f"~{round(sum(e.get('calories_burned') or 0 for e in exercises))} kcal burned. "
        f"Symptoms logged: {len(symptoms)}."
    )


def _fmt_time(iso: str) -> str:
    try:
        return f"{datetime.fromisoformat(iso):%Y-%m-%d %H:%M}"
    except (ValueError, TypeError):
        return iso or "unknown"


def recent_meals_report(conn: sqlite3.Connection, limit: int = 3) -> str:
    meals = diary.recent_meals(conn, limit)
    if not meals:
        return "No meals logged yet."
    lines = [f"Last {len(meals)} meals:"]
    for m in meals:
        kcal = round(sum(i.get("calories") or 0 for i in m["items"]))
        names = ", ".join(i["name"] for i in m["items"]) or "(no items)"
        lines.append(f"- {_fmt_time(m['occurred_at'])}: {names} (~{kcal} kcal)")
    return "\n".join(lines)


def recent_symptoms_report(conn: sqlite3.Connection, limit: int = 3) -> str:
    symptoms = diary.recent_symptoms(conn, limit)
    if not symptoms:
        return "No symptoms logged yet."
    lines = [f"Last {len(symptoms)} symptoms:"]
    for s in symptoms:
        severity = f" (severity {s['severity']}/5)" if s.get("severity") else ""
        lines.append(f"- {_fmt_time(s['occurred_at'])}: {s['type']}{severity}")
    return "\n".join(lines)


def recent_exercises_report(conn: sqlite3.Connection, limit: int = 3) -> str:
    exercises = diary.recent_exercises(conn, limit)
    if not exercises:
        return "No workouts logged yet."
    lines = [f"Last {len(exercises)} workouts:"]
    for e in exercises:
        details = []
        if e.get("duration_min"):
            details.append(f"{round(e['duration_min'])} min")
        if e.get("intensity"):
            details.append(e["intensity"])
        if e.get("calories_burned"):
            details.append(f"~{round(e['calories_burned'])} kcal")
        suffix = f" ({', '.join(details)})" if details else ""
        lines.append(f"- {_fmt_time(e['occurred_at'])}: {e['type']}{suffix}")
    return "\n".join(lines)


def recent_notes_report(diet_path: Path, limit: int = 3) -> str:
    notes = memory_diet.recent_notes(diet_path, limit)
    if not notes:
        return "No notes yet."
    lines = []
    for raw in notes:
        parsed = memory_diet.parse_note_line(raw)
        if parsed:
            lines.append(f"- {_fmt_time(parsed['occurred_at'].isoformat())}: {parsed['text']}")
        else:
            lines.append(raw)  # pre-timestamp legacy note — show as stored
    return f"Last {len(lines)} notes:\n" + "\n".join(lines)
