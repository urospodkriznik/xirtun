"""Deterministic diary reports for the /today and /week commands (no LLM involved)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

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
