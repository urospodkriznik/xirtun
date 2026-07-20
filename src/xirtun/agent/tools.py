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
    "- get_intake_summary(weeks:int=4) -> this week's per-day intake table, a "
    "week-over-week comparison across recent weeks (avg kcal/protein/fibre per logged "
    "day, with this-week-vs-last-week deltas), the working-target check, and late-evening "
    "meals — all computed in SQL. USE THESE NUMBERS — never sum meal items yourself\n"
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


def _week_label(weeks_ago: int) -> str:
    if weeks_ago == 0:
        return "This week"
    if weeks_ago == 1:
        return "1 wk ago"
    return f"{weeks_ago} wks ago"


def _intake_summary(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Deterministic intake numbers: this week's per-day detail, a week-over-week
    comparison across the last `weeks` weeks, and the working-target check. Everything
    is computed in code so the agent's energy/macro claims (and the week-over-week
    deltas) rest on real arithmetic, not the model summing dozens of items itself."""
    weeks = int(args.get("weeks", 4))

    since_week = (ctx.now - timedelta(days=7)).isoformat()
    day_rows = diary.daily_totals(ctx.conn, since_week)
    if not day_rows and not any(w["days_logged"] for w in diary.weekly_totals(ctx.conn, ctx.now, weeks=weeks)):
        return f"No meals logged in the last {weeks} weeks."

    lines = ["This week's per-day intake (SQL-computed):"]
    if day_rows:
        for r in day_rows:
            lines.append(
                f"- {r['day']}: {r['meals']} meal(s), ~{round(r['calories'])} kcal, "
                f"{round(r['protein_g'])}g protein, {round(r['fiber_g'])}g fibre"
            )
        lines.append(
            f"Days logged this week: {len(day_rows)} of 7. (Judge whether sparse days "
            "mean incomplete logging by comparing meal counts to the user's usual "
            "pattern, rather than treating a thin day as a real fast.)"
        )
    else:
        lines.append("- (nothing logged this week)")

    # Week-over-week: averages per LOGGED day, most recent first, with the delta from
    # this week to last week spelled out so trend — not a single week — drives the read.
    wk = diary.weekly_totals(ctx.conn, ctx.now, weeks=weeks)
    lines.append("\nWeek-over-week (avg per logged day, most recent first):")
    for w in wk:
        if w["days_logged"]:
            lines.append(
                f"- {_week_label(w['weeks_ago'])}: ~{round(w['avg_calories'])} kcal, "
                f"{round(w['avg_protein_g'])}g protein, {round(w['avg_fiber_g'])}g fibre "
                f"({w['days_logged']} day(s) logged)"
            )
        else:
            lines.append(f"- {_week_label(w['weeks_ago'])}: nothing logged")

    this_wk, last_wk = wk[0], (wk[1] if len(wk) > 1 else None)
    if this_wk["days_logged"] and last_wk and last_wk["days_logged"]:
        d_cal = this_wk["avg_calories"] - last_wk["avg_calories"]
        pct = round(d_cal / last_wk["avg_calories"] * 100) if last_wk["avg_calories"] else 0
        lines.append(
            f"This week vs last week: {d_cal:+.0f} kcal ({pct:+d}%), "
            f"{this_wk['avg_protein_g'] - last_wk['avg_protein_g']:+.0f}g protein, "
            f"{this_wk['avg_fiber_g'] - last_wk['avg_fiber_g']:+.0f}g fibre."
        )

    target = targets.working_target(ctx.conn)
    if target is not None and this_wk["days_logged"]:
        lines.append(
            f"\nWorking target ({target['source']}): ~{target['calories']} kcal/day, "
            f"{target['protein_min_g']}–{target['protein_max_g']}g protein/day → this "
            f"week averages {round(this_wk['avg_calories'] / target['calories'] * 100)}% "
            "of target calories. Reconcile against the weight trend before calling it a "
            "deficit or surplus."
        )

    late = diary.late_meal_days(ctx.conn, since_week)
    lines.append(
        "\nMeals eaten at/after 20:00 this week (reflux window): "
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
