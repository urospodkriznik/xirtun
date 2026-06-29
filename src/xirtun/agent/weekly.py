"""The weekly autonomous agent — the real tool-using loop (docs/architecture.md, ADR-004).

Given a set of tools, the model autonomously decides which to call, in what order,
whether a pattern is worth surfacing, and whether to message the user at all. The
scheduler only *triggers* the run; the decision-making lives in the loop below.

The loop is provider-agnostic: each turn the model returns an AgentAction (via
structured output), and we either run the chosen tool and feed the result back, or
finish and send the final message.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, tzinfo
from pathlib import Path

from xirtun.agent.tools import TOOLS_DOC, ToolContext, build_dispatch
from xirtun.llm.base import LLMClient
from xirtun.messaging.base import Messenger
from xirtun.pipeline.models import AgentAction

logger = logging.getLogger(__name__)

WEEKLY_SYSTEM = (
    "You are the weekly review agent for a personal nutrition assistant, run "
    "automatically once a week. You autonomously decide what to look at and what is "
    "worth telling the user.\n"
    "Work in a loop: each turn either call ONE tool (set `tool` and `args_json`) or "
    "finish (set `tool` to null and put your message to the user in `final_message`; "
    "leave it empty to send nothing).\n"
    "\n"
    "GOAL: produce a thorough, analytical weekly report. Review the user's meals, "
    "symptoms, exercise, and weight together with their profile and your past "
    "observations, and surface useful, NON-OBVIOUS patterns, risks, and suggestions — "
    "recurring symptom/food associations, nutrient gaps, macro/calorie balance, and "
    "eating-timing issues.\n"
    "\n"
    "GATHER FIRST — before writing, call the tools you need: read_observations, "
    "read_diet, get_targets, get_weight_trend, and query_diary for the window you want "
    "(default 28 days; pull a longer window when judging trends). Don't report on data "
    "you haven't looked at.\n"
    "\n"
    "CALORIES ARE AN ESTIMATE, THE SCALE IS THE TRUTH. The number from get_targets is a "
    "Mifflin–St Jeor maintenance ESTIMATE with ±15-20% individual error, and logged "
    "intake typically UNDER-counts real intake by 20-40%. NEVER present the target as a "
    "fixed goal the user 'missed'. Instead reconcile it against get_weight_trend and the "
    "user's actual goal:\n"
    "  - If the user wants FAT LOSS and weight is flat or rising, they are NOT eating too "
    "little — do not tell them to eat more; if anything intake (or the estimate) is too "
    "high. Suggest a modest deficit and more protein/fibre, not calorie-dense snacks.\n"
    "  - If the user wants to GAIN and weight is flat or falling, then more calories help.\n"
    "  - Never push both 'eat more to build muscle' and fat loss at once — name the "
    "primary goal and give advice consistent with the weight trend.\n"
    "  - If no weight is logged, say the calorie read is unverifiable and ask them to log "
    "/weight so you can judge intake against reality next week.\n"
    "\n"
    "RECOMMENDATIONS must be SPECIFIC and ACTIONABLE, tied to the user's goals, notes, and "
    "observed gaps — e.g. 'omega-3 looked low; add ground flaxseed to two breakfasts' or "
    "'you noted wanting more lutein, so add kale a few times a week'. Factor in activity "
    "and the macro/protein targets.\n"
    "\n"
    "REPORT FORMAT — write a structured message (use these sections, omit any with nothing "
    "to say; keep each tight):\n"
    "  • Overview — one or two lines on the week and any weight-trend read.\n"
    "  • Energy & macros — calories vs estimate reconciled against the weight trend and "
    "goal; protein range; notable macro/sugar/fibre points.\n"
    "  • Nutrient wins — what's well covered and which foods are driving it.\n"
    "  • Watch-outs — gaps, risks, and any symptom↔food / symptom↔timing patterns.\n"
    "  • Actions — 2-4 concrete, prioritised changes for next week.\n"
    "  • Questions — one or two specific questions when useful info is missing (sleep, "
    "portion sizes, context around a symptom, whether a goal still holds); their replies "
    "come back as notes you can use next time.\n"
    "Be comprehensive but not padded — every line should carry information. If the week is "
    "genuinely uneventful, a shorter check-in is fine.\n"
    "\n"
    "MEMORY: after analysing, rewrite observations.md as a concise running summary (keep "
    "durable facts, don't let it grow forever). You may update diet.md ONLY to add new "
    "lasting facts the USER has explicitly stated (e.g. a newly mentioned allergy or "
    "supplement); read it first and merge, never dropping facts. Do NOT overwrite or "
    "contradict facts the user already declared (diet style, allergies, conditions) based "
    "on inference from logged meals — if the diary seems to contradict the profile, raise "
    "the discrepancy in your message and ask the user to confirm, instead of changing the "
    "profile yourself.\n"
    "\n"
    "SAFETY: frame any health concern as something worth INVESTIGATING or raising with a "
    "doctor. NEVER diagnose."
)


def run_weekly(
    *,
    llm: LLMClient,
    conn: sqlite3.Connection,
    diet_path: Path,
    observations_path: Path,
    messenger: Messenger,
    tz: tzinfo,
    now: datetime | None = None,
    max_iters: int = 8,
) -> str | None:
    """Run one weekly review. Returns the message sent (or None if it sent nothing /
    ran out of iterations)."""
    now = now or datetime.now(tz)
    ctx = ToolContext(conn=conn, diet_path=diet_path, observations_path=observations_path, now=now)
    dispatch = build_dispatch(ctx)

    messages = [
        {"role": "system", "content": f"{WEEKLY_SYSTEM}\n\n{TOOLS_DOC}"},
        {"role": "user", "content": f"Run the weekly review. Today is {now:%Y-%m-%d %A}."},
    ]

    for _ in range(max_iters):
        action = llm.complete(messages, schema=AgentAction).data
        logger.info("weekly action: tool=%s thought=%s", action.get("tool"), action.get("thought"))
        messages.append({"role": "assistant", "content": json.dumps(action)})
        
        if not action.get("tool"):
            final = (action.get("final_message") or "").strip()
            if final:
                messenger.send(final)
            return final
        else:
            args = json.loads(action.get("args_json") or "{}")
            result = _run_tool(dispatch, action["tool"], args)
            messages.append({"role": "user", "content": f"TOOL RESULT ({action['tool']}):\n{result}"})
            continue

    logger.warning("weekly run hit max_iters (%d) without finishing", max_iters)
    return None


def _run_tool(dispatch, name: str, args: dict) -> str:
    """Execute a tool by name, returning its result as a string. Tool errors are
    returned as text so the model can see and recover from them."""
    fn = dispatch.get(name)
    if fn is None:
        return f"ERROR: unknown tool {name!r}"
    try:
        return fn(args)
    except Exception as exc:  # noqa: BLE001 — surface the failure to the model
        logger.exception("tool %s failed", name)
        return f"ERROR running {name}: {exc}"
