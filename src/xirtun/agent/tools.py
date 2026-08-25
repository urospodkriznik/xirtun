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
    "- get_intake_summary(weeks:int=12) -> this week's per-day intake table, a "
    "week-over-week comparison across the whole window (avg kcal AND protein/fat/carbs/"
    "sugar/fibre per logged day, with deltas), the working-target check, when each "
    "nutrient started being tracked, late-evening meals and what share of energy lands "
    "after 20:00 — all computed in SQL. USE THESE NUMBERS — never sum meal items yourself\n"
    "- get_food_frequency(days:int=90) -> how often each food appears (times, and on how "
    "many of the logged days), with the day count as denominator — the basis for any "
    "'you rarely eat X' claim\n"
    "- compare_symptom_days(symptom:str, days:int=90) -> days with that symptom vs days "
    "without, compared on calories, evening calories, fibre and sugar, with the day count "
    "so you can judge whether the difference is worth anything\n"
    "- query_diary(since_days:int=28, kind:'all'|'meals'|'symptoms'|'exercises'='all') -> recent diary\n"
    "- read_diet() -> the user's profile (diet.md)\n"
    "- read_observations() -> your own prior notes (observations.md)\n"
    "- write_observations(content:str) -> replace your notes with an updated, concise summary\n"
    "- update_diet(content:str) -> replace the profile (read it first; merge, never drop facts)\n"
    "- get_targets() -> the formula ESTIMATE plus the current calibrated working target\n"
    "- set_targets(calories:int, protein_min_g:int, protein_max_g:int, rationale:str) -> "
    "persist a new calibrated working target (clamped to safe bounds; rationale required)\n"
    "- get_weight_trend(days:int=56) -> the user's logged weight AND waist trends over "
    "the window (waist is what separates fat loss from muscle loss when weight moves)\n"
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
    weeks = int(args.get("weeks", 12))

    since_week = (ctx.now - timedelta(days=7)).isoformat()
    day_rows = diary.daily_totals(ctx.conn, since_week)
    if not day_rows and not any(w["days_logged"] for w in diary.weekly_totals(ctx.conn, ctx.now, weeks=weeks)):
        return f"No meals logged in the last {weeks} weeks."

    lines = ["This week's per-day intake (SQL-computed):"]
    if day_rows:
        for r in day_rows:
            lines.append(
                f"- {r['day']}: {r['meals']} meal(s), ~{round(r['calories'])} kcal, "
                f"{round(r['protein_g'])}g protein, {round(r['fat_g'])}g fat, "
                f"{round(r['carbs_g'])}g carbs, {round(r['sugar_g'])}g sugar, "
                f"{round(r['fiber_g'])}g fibre"
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
    lines.append(
        f"\nWeek-over-week over {weeks} weeks (avg per logged day, most recent first). "
        "Read DOWN this list for slow trends — a macro that climbs or falls steadily "
        "across many weeks is invisible in any single week's numbers:"
    )
    for w in wk:
        if w["days_logged"]:
            lines.append(
                f"- {_week_label(w['weeks_ago'])}: ~{round(w['avg_calories'])} kcal, "
                f"{round(w['avg_protein_g'])}g protein, {round(w['avg_fat_g'])}g fat, "
                f"{round(w['avg_carbs_g'])}g carbs, {round(w['avg_sugar_g'])}g sugar, "
                f"{round(w['avg_fiber_g'])}g fibre ({w['days_logged']} day(s) logged)"
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
            f"{this_wk['avg_sugar_g'] - last_wk['avg_sugar_g']:+.0f}g sugar, "
            f"{this_wk['avg_fiber_g'] - last_wk['avg_fiber_g']:+.0f}g fibre."
        )

    # When a nutrient started being recorded. Averaging across that boundary silently
    # understates it, and the agent has no way to know where the data begins.
    starts = diary.tracking_start_dates(ctx.conn)
    late_starts = {
        macro: day for macro, day in starts.items()
        if day and day > (ctx.now - timedelta(days=weeks * 7)).date().isoformat()
    }
    if late_starts:
        detail = ", ".join(f"{macro.replace('_g', '')} from {day}" for macro, day in late_starts.items())
        lines.append(
            f"\nTracking began mid-window for: {detail}. Entries before those dates carry "
            "no value for that nutrient, so any average spanning them UNDERSTATES it — "
            "judge those nutrients only from weeks after they start."
        )
    never = [macro.replace("_g", "") for macro, day in starts.items() if day is None]
    if never:
        lines.append(
            f"\nNEVER RECORDED in any entry: {', '.join(never)}. The zeros above are "
            "absence of data, not measured zeros — say the diary can't answer for these "
            "rather than reporting a shortfall the user never had."
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
    share = diary.evening_calorie_share(ctx.conn, (ctx.now - timedelta(days=weeks * 7)).isoformat())
    if share["total_calories"]:
        lines.append(
            f"Across the whole {weeks}-week window, {round(share['late_share'] * 100)}% of "
            f"logged energy was eaten at/after {share['hour']}:00 "
            f"(~{round(share['late_calories'])} of {round(share['total_calories'])} kcal). "
            "How MUCH lands late matters more than whether it happened."
        )
    return "\n".join(lines)


def format_weekly_numbers(
    conn: sqlite3.Connection, now: datetime, *, weeks: int = 12
) -> str:
    """The week-by-week figures, formatted for the app to write into observations.md.

    Same arithmetic the agent reads through get_intake_summary, but written by the app
    so the numbers in its memory are never a transcription. See
    `observations.write_numbers_block` for why that matters.
    """
    lines = [
        "## Verified weekly numbers (written by the app, not the agent)",
        "",
        f"Averages per LOGGED day, most recent week first — as of {now:%Y-%m-%d}.",
        "",
        "| Week | Days logged | kcal | Protein | Fat | Carbs | Sugar | Fibre |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for w in diary.weekly_totals(conn, now, weeks=weeks):
        if not w["days_logged"]:
            continue
        lines.append(
            f"| {_week_label(w['weeks_ago'])} | {w['days_logged']} | "
            f"{round(w['avg_calories'])} | {round(w['avg_protein_g'])} | "
            f"{round(w['avg_fat_g'])} | {round(w['avg_carbs_g'])} | "
            f"{round(w['avg_sugar_g'])} | {round(w['avg_fiber_g'])} |"
        )
    lines += [
        "",
        "These figures are recomputed from the diary every run. If your prose above "
        "disagrees with this table, the table is right.",
    ]
    return "\n".join(lines)


def verified_values(conn: sqlite3.Connection, now: datetime, *, weeks: int = 12) -> set[int]:
    """Every calorie figure the diary actually supports: per-day totals and per-week
    averages. Used to check the agent's report against arithmetic it can't fudge."""
    since = (now - timedelta(days=weeks * 7)).isoformat()
    values = {round(row["calories"]) for row in diary.daily_totals(conn, since)}
    values |= {
        round(w["avg_calories"]) for w in diary.weekly_totals(conn, now, weeks=weeks)
        if w["days_logged"]
    }
    target = targets.working_target(conn)
    if target is not None:
        values.add(int(target["calories"]))
    return values


def _food_frequency(ctx: ToolContext, args: dict[str, Any]) -> str:
    """How often each food actually appears, with the denominator alongside — the basis
    for any 'you rarely eat X' claim."""
    days = int(args.get("days", 90))
    since = (ctx.now - timedelta(days=days)).isoformat()
    rows = diary.food_frequency(ctx.conn, since)
    logged = diary.logged_day_count(ctx.conn, since)
    if not rows:
        return f"No meals logged in the last {days} days."

    lines = [
        f"Food frequency over the last {days} days ({logged} day(s) actually logged — "
        "that is the denominator for every claim below):",
    ]
    for r in rows:
        lines.append(
            f"- {r['name']}: {r['times']}x on {r['days']} of {logged} logged day(s), "
            f"~{round(r['calories'])} kcal total"
        )
    lines.append(
        "Absence is evidence too: a food that never appears is a gap you can name, but "
        "only within what was logged."
    )
    return "\n".join(lines)


def _symptom_patterns(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Days with a symptom vs days without, on the numbers that could plausibly explain
    it. Computed here so the comparison is real; whether it MEANS anything is judgement."""
    symptom = str(args.get("symptom", "")).strip()
    if not symptom:
        return "ERROR: name the symptom to compare (e.g. 'bloating')."
    days = int(args.get("days", 90))
    since = (ctx.now - timedelta(days=days)).isoformat()
    c = diary.symptom_day_comparison(ctx.conn, symptom, since)
    with_s, without = c["with_symptom"], c["without"]

    if not with_s["days"]:
        return f"No days with '{symptom}' logged in the last {days} days."

    def row(label: str, group: dict[str, Any]) -> str:
        return (
            f"- {label} ({group['days']} day(s)): ~{round(group['calories'])} kcal, "
            f"~{round(group['evening_calories'])} kcal after 20:00, "
            f"{round(group['fiber_g'])}g fibre, {round(group['sugar_g'])}g sugar"
        )

    return "\n".join([
        f"Days WITH '{symptom}' vs days without, last {days} days:",
        row(f"with {symptom}", with_s),
        row("without", without),
        "",
        f"n = {with_s['days']} symptom day(s). With a handful of days, a difference of "
        "this size is a hypothesis, not a finding — say so, and give the size of the "
        "difference rather than implying a link.",
    ])


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
        "get_food_frequency": lambda a: _food_frequency(ctx, a),
        "compare_symptom_days": lambda a: _symptom_patterns(ctx, a),
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
        # Weight and waist together: the scale alone can't tell fat loss from muscle
        # loss, which is exactly the question the user keeps asking.
        "get_weight_trend": lambda a: (
            targets.format_weight_trend(ctx.conn, now=ctx.now, days=int(a.get("days", 56)))
            + "\n"
            + targets.format_waist_trend(ctx.conn, now=ctx.now, days=int(a.get("days", 56)))
        ),
    }
