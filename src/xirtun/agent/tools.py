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
    "- query_diary(since_days:int=28, kind:'all'|'meals'|'symptoms'|'exercises'='all') -> recent diary\n"
    "- read_diet() -> the user's profile (diet.md)\n"
    "- read_observations() -> your own prior notes (observations.md)\n"
    "- write_observations(content:str) -> replace your notes with an updated, concise summary\n"
    "- update_diet(content:str) -> replace the profile (read it first; merge, never drop facts)\n"
    "- get_targets() -> the user's computed daily calorie & protein targets (an ESTIMATE)\n"
    "- get_weight_trend(days:int=56) -> the user's logged weight trend over the window\n"
)


def _format_meals(meals: list[dict[str, Any]]) -> str:
    if not meals:
        return "Meals: (none)"
    lines = ["Meals:"]
    for m in meals:
        items = ", ".join(
            f"{i['name']} (~{round(i['calories'] or 0)}kcal, tags={json.loads(i['tags'] or '[]')})"
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


def _write_observations(ctx: ToolContext, args: dict[str, Any]) -> str:
    observations.write(ctx.observations_path, args["content"])
    return "saved"


def _update_diet(ctx: ToolContext, args: dict[str, Any]) -> str:
    diet_memory.write_diet(ctx.diet_path, args["content"], now=ctx.now)
    return "saved"


def build_dispatch(ctx: ToolContext) -> dict[str, Callable[[dict[str, Any]], str]]:
    """Return the agent's toolbox: tool name -> function(args) -> result string."""
    return {
        "query_diary": lambda a: _query_diary(ctx, a),
        "read_diet": lambda a: diet_memory.read_diet(ctx.diet_path) or "(empty)",
        "read_observations": lambda a: observations.read(ctx.observations_path) or "(empty)",
        "write_observations": lambda a: _write_observations(ctx, a),
        "update_diet": lambda a: _update_diet(ctx, a),
        "get_targets": lambda a: targets.format_targets(targets.read_metrics(ctx.conn)),
        "get_weight_trend": lambda a: targets.format_weight_trend(
            ctx.conn, now=ctx.now, days=int(a.get("days", 56))
        ),
    }
