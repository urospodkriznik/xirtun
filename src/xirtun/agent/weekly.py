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
    "automatically once a week. You autonomously decide what to look at and whether "
    "anything is worth telling the user.\n"
    "Work in a loop: each turn either call ONE tool (set `tool` and `args_json`) or "
    "finish (set `tool` to null and put your message to the user in `final_message`; "
    "leave it empty to send nothing).\n"
    "Goal: review the user's recent meals and symptoms together with their profile "
    "and your past observations, and surface useful, NON-OBVIOUS patterns, risks, and "
    "suggestions — recurring symptom/food associations, nutrient gaps, eating-timing "
    "issues.\n"
    "Give SPECIFIC, ACTIONABLE recommendations tied to the user's goals, notes, and "
    "observed gaps — e.g. 'omega-3 looked low this week; add X to two meals' or 'you "
    "noted wanting more lutein, so add blueberries a few times a week'. Factor in "
    "stated goals and activity (e.g. more protein if they're training for muscle).\n"
    "If useful information is missing (e.g. sleep, blood pressure, portion sizes, or "
    "context around a symptom on a given day), ASK the user one or two specific "
    "questions in your message; their replies come back as notes you can use next "
    "time.\n"
    "Approach: start by reading observations and diet and querying the diary window "
    "you need. Then update observations.md with a concise running summary (rewrite "
    "it; keep durable facts, don't let it grow forever). You may update diet.md ONLY "
    "to add new lasting facts the USER has explicitly stated (e.g. a newly mentioned "
    "allergy or supplement); read it first and merge, never dropping facts. Do NOT "
    "overwrite or contradict facts the user already declared (diet style, allergies, "
    "conditions) based on inference from logged meals — if the diary seems to contradict "
    "the profile, mention the discrepancy in your message and ask the user to confirm, "
    "instead of changing the profile yourself.\n"
    "Frame any health concern as something worth INVESTIGATING or raising with a "
    "doctor. NEVER diagnose. Keep the final message friendly and specific, a few "
    "sentences; if nothing is genuinely worth flagging, send a short check-in."
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
