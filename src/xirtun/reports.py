"""Deterministic diary reports for the /today and /week commands (no LLM involved)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from xirtun.memory import diet as memory_diet
from xirtun.storage import diary

_MACROS = ("calories", "protein_g", "fat_g", "carbs_g")


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
        f"{round(t['carbs_g'])}g carbs):"
    ]
    for meal in meals:
        names = ", ".join(item["name"] for item in meal["items"])
        lines.append(f"- {names}")
    return "\n".join(lines)


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
        f"(~{round(t['protein_g'] / days_logged)}/day). "
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
    return f"Last {len(notes)} notes:\n" + "\n".join(notes)
