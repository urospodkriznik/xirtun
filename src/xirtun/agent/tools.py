"""Tools the weekly agent can call, plus the dispatch table.

Each tool is a plain function taking (ctx, args_dict) and returning a STRING the
model reads next turn. `build_dispatch` binds the context and returns a
{name: callable} dict — that dict IS the agent's toolbox. TOOLS_DOC is the
human-readable description we put in the system prompt so the model knows what's
available.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from xirtun import targets
from xirtun.memory import diet as diet_memory
from xirtun.memory import observations
from xirtun.storage import diary


@dataclass
class ToolContext:
    conn: sqlite3.Connection
    diet_path: Path
    observations_path: Path
    now: datetime


TOOLS_DOC = (
    "Tools (call exactly ONE per turn, via `tool` + `args_json`):\n"
    "- get_intake_summary(since_days:int=7) -> per-day intake table (meals, kcal, protein, "
    "fibre) computed in SQL, plus averages, the working target comparison, and late-evening "
    "meals. USE THESE NUMBERS — never sum meal items yourself\n"
    "- query_diary(since_days:int=28, kind:'all'|'meals'|'symptoms'|'exercises'='all') -> recent diary\n"
    "- read_diet() -> the user's profile (diet.md)\n"
    "- read_observations() -> your own prior notes (observations.md)\n"
    "- write_observations(content:str) -> replace your notes with an updated, concise summary\n"
    "- update_diet(content:str) -> replace the profile (read it first; merge, never drop facts)\n"
    "- get_targets() -> the formula ESTIMATE plus the current calibrated working target\n"
    "- set_targets(calories:int, protein_min_g:int, protein_max_g:int, rationale:str) -> "
    "persist a new calibrated working target (clamped to safe bounds; rationale required)\n"
    "- get_weight_trend(days:int=56) -> the user's logged weight trend over the window\n"
)


def _format_meals(meals: list[dict[str, Any]]) -> str:
    if not meals:
        return "Meals: (none)"
    lines = ["Meals:"]
    for m in meals:
        items = ", ".join(
            f"{i['name']} (~{round(i['calories'] or 0)}kcal, "
            f"{round(i.get('protein_g') or 0)}g protein, "
            f"{round(i.get('fiber_g') or 0)}g fibre, "
            f"tags={json.loads(i['tags'] or '[]')})"
            for i in m["items"]
        )
        lines.append(f"- {m['occurred_at']}: {items}")
    return "\n".join(lines)


def _format_symptoms(symptoms: list[dict[str, Any]]) -> str:
    if not symptoms:
        return "Symptoms: (none)"
    lines = ["Symptoms:"]
    for s in symptoms:
        severity = f" severity={s['severity']}" if s["severity"] else ""
        lines.append(f"- {s['occurred_at']}: {s['type']}{severity} tags={json.loads(s['tags'] or '[]')}")
    return "\n".join(lines)


def _format_exercises(exercises: list[dict[str, Any]]) -> str:
    if not exercises:
        return "Exercise: (none)"
    lines = ["Exercise:"]
    for e in exercises:
        bits = [e["type"]]
        if e.get("duration_min"):
            bits.append(f"{round(e['duration_min'])}min")
        if e.get("intensity"):
            bits.append(e["intensity"])
        if e.get("calories_burned"):
            bits.append(f"~{round(e['calories_burned'])}kcal")
        lines.append(f"- {e['occurred_at']}: " + ", ".join(bits))
    return "\n".join(lines)


def _query_diary(ctx: ToolContext, args: dict[str, Any]) -> str:
    days = int(args.get("since_days", 28))
    kind = args.get("kind", "all")
    since = (ctx.now - timedelta(days=days)).isoformat()
    parts = []
    if kind in ("all", "meals"):
        parts.append(_format_meals(diary.meals_since(ctx.conn, since)))
    if kind in ("all", "symptoms"):
        parts.append(_format_symptoms(diary.symptoms_since(ctx.conn, since)))
    if kind in ("all", "exercises"):
        parts.append(_format_exercises(diary.exercises_since(ctx.conn, since)))
    return "\n\n".join(parts)


def _intake_summary(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Deterministic per-day intake numbers + working-target comparison. Everything
    here is computed in code so the agent's energy/macro claims rest on real
    arithmetic, not the model summing dozens of items itself."""
    days = int(args.get("since_days", 7))
    since = (ctx.now - timedelta(days=days)).isoformat()

    rows = diary.daily_totals(ctx.conn, since)
    if not rows:
        return f"No meals logged in the last {days} days."

    lines = [f"Per-day intake, last {days} days (SQL-computed):"]
    for r in rows:
        lines.append(
            f"- {r['day']}: {r['meals']} meal(s), ~{round(r['calories'])} kcal, "
            f"{round(r['protein_g'])}g protein, {round(r['fiber_g'])}g fibre"
        )
    n = len(rows)
    avg_kcal = sum(r["calories"] for r in rows) / n
    avg_protein = sum(r["protein_g"] for r in rows) / n
    avg_fiber = sum(r["fiber_g"] for r in rows) / n
    lines.append(
        f"Averages over the {n} day(s) WITH logged meals: ~{round(avg_kcal)} kcal/day, "
        f"{round(avg_protein)}g protein/day, {round(avg_fiber)}g fibre/day. "
        f"({days - n} day(s) in the window have nothing logged — judge whether sparse "
        "days mean incomplete logging by comparing meal counts to the user's usual "
        "pattern, and say so rather than treating a thin day as a real fast.)"
    )

    target = targets.working_target(ctx.conn)
    if target is not None:
        lines.append(
            f"Working target ({target['source']}): ~{target['calories']} kcal/day, "
            f"{target['protein_min_g']}–{target['protein_max_g']}g protein/day → logged "
            f"intake averages {round(avg_kcal / target['calories'] * 100)}% of target "
            f"calories. Reconcile this against the weight trend before calling it a "
            "deficit or surplus."
        )

    late = diary.late_meal_days(ctx.conn, since)
    lines.append(
        "Meals eaten at/after 20:00 (reflux window): "
        + (", ".join(late) if late else "none")
    )
    return "\n".join(lines)


def _write_observations(ctx: ToolContext, args: dict[str, Any]) -> str:
    observations.write(ctx.observations_path, args["content"])
    return "saved"


def _update_diet(ctx: ToolContext, args: dict[str, Any]) -> str:
    diet_memory.write_diet(ctx.diet_path, args["content"], now=ctx.now)
    return "saved"


def build_dispatch(ctx: ToolContext) -> dict[str, Callable[[dict[str, Any]], str]]:
    """Return the agent's toolbox: tool name -> function(args) -> result string."""
    return {
        "get_intake_summary": lambda a: _intake_summary(ctx, a),
        "query_diary": lambda a: _query_diary(ctx, a),
        "read_diet": lambda a: diet_memory.read_diet(ctx.diet_path) or "(empty)",
        "read_observations": lambda a: observations.read(ctx.observations_path) or "(empty)",
        "write_observations": lambda a: _write_observations(ctx, a),
        "update_diet": lambda a: _update_diet(ctx, a),
        "get_targets": lambda a: targets.format_all_targets(ctx.conn),
        "set_targets": lambda a: targets.set_calibrated(
            ctx.conn,
            calories=a["calories"],
            protein_min_g=a["protein_min_g"],
            protein_max_g=a["protein_max_g"],
            rationale=a.get("rationale", ""),
            now=ctx.now,
        ),
        "get_weight_trend": lambda a: targets.format_weight_trend(
            ctx.conn, now=ctx.now, days=int(a.get("days", 56))
        ),
    }
