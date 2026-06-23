"""The hot-path orchestrator — the reactive state machine from docs/architecture.md.

Handles meals and symptoms through one shape: classify -> (clarify?) -> structure
-> store. A single message may describe several meals or symptoms, each stored as
its own row (separate-events model).

Sessions remember whether we're mid-MEAL or mid-SYMPTOM (session.kind), so a
follow-up answer is routed to the right processor.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from xirtun.llm.base import LLMClient
from xirtun.messaging.base import Messenger
from xirtun.memory import diet as memory
from xirtun.pipeline import sessions
from xirtun.pipeline.classify import classify
from xirtun.pipeline.onboarding import onboarding_step
from xirtun.pipeline.structure import structure_meal
from xirtun.pipeline.symptom import structure_symptom
from xirtun import reports
from xirtun.storage import admin, diary

logger = logging.getLogger(__name__)

MEAL_COMMANDS = {"/meal", "/new"}

HELP_TEXT = (
    "Just tell me what you ate or how you feel, in plain language, and I'll log it. "
    "You can also share goals or notes (e.g. 'I want to gain muscle').\n\n"
    "Commands:\n"
    "/meal — start a new meal entry\n"
    "/undo — remove your last entry\n"
    "/today — today's meals and totals\n"
    "/week — the past 7 days\n"
    "/profile — show your profile\n"
    "/weekly — run your weekly review now\n"
    "/clear-data — erase everything (asks to confirm)"
)


def format_ack(meals: list[dict[str, Any]]) -> str:
    """Confirmation summarizing the meal(s) just logged."""
    summaries = []
    for meal in meals:
        items = meal["items"]
        total_cal = sum(i.get("calories") or 0 for i in items)
        names = ", ".join(i["name"] for i in items)
        summaries.append(f"{names} (~{round(total_cal)} kcal)")

    if len(summaries) == 1:
        return f"Logged: {summaries[0]}."
    return f"Logged {len(summaries)} meals:\n- " + "\n- ".join(summaries)


def format_symptom_ack(symptoms: list[dict[str, Any]]) -> str:
    """Confirmation summarizing the symptom(s) just logged."""
    parts = []
    for s in symptoms:
        severity = f" (severity {s['severity']}/5)" if s.get("severity") else ""
        parts.append(f"{s['type']}{severity}")
    return "Noted: " + ", ".join(parts) + "."


def handle_message(
    text: str,
    *,
    chat_id: str,
    llm: LLMClient,
    conn: sqlite3.Connection,
    messenger: Messenger,
    diet_path: Path | None = None,
    now: datetime | None = None,
) -> None:
    text = text.strip()

    # 1) Explicit "start a new meal" command.
    if text in MEAL_COMMANDS:
        sessions.upsert(conn, chat_id, "meal", "", now=now)
        messenger.send("New meal — tell me what you ate.")
        return

    if text == "/undo":
        deleted = diary.delete_last(conn)
        messenger.send(f"Removed your last entry ({deleted})." if deleted else "Nothing to undo.")
        return

    if text == "/help":
        messenger.send(HELP_TEXT)
        return
    if text == "/profile":
        profile = memory.read_diet(diet_path) if diet_path else ""
        messenger.send(profile or "No profile yet — just start logging and I'll build one.")
        return
    if text == "/today":
        messenger.send(reports.today_report(conn, now or datetime.now().astimezone()))
        return
    if text == "/week":
        messenger.send(reports.week_report(conn, now or datetime.now().astimezone()))
        return

    # 2) Mid-session: continue whatever we were collecting (meal or symptom).
    session = sessions.get_active(conn, chat_id, now=now)
    if session is not None:
        combined = f"{session.text}\n{text}".strip()
        if session.kind == "symptom":
            _process_symptom(combined, chat_id=chat_id, llm=llm, conn=conn, messenger=messenger, now=now)
        else:
            _process_meal(combined, chat_id=chat_id, llm=llm, conn=conn, messenger=messenger, now=now)
        return

    # 3) No session: classify fresh and route.
    intent = classify(llm, text)
    logger.info("intent=%s", intent)
    if intent == "meal":
        _process_meal(text, chat_id=chat_id, llm=llm, conn=conn, messenger=messenger, now=now)
    elif intent == "symptom":
        _process_symptom(text, chat_id=chat_id, llm=llm, conn=conn, messenger=messenger, now=now)
    elif intent == "note" and diet_path is not None:
        _process_note(text, diet_path=diet_path, messenger=messenger, now=now)
    else:
        messenger.send(
            "I'm not sure what that was — tell me what you ate, how you're feeling, "
            "or a goal/note to remember."
        )


def _process_meal(
    text: str,
    *,
    chat_id: str,
    llm: LLMClient,
    conn: sqlite3.Connection,
    messenger: Messenger,
    now: datetime | None = None,
) -> None:
    draft = structure_meal(llm, text, now=now)

    if draft["needs_clarification"]:
        sessions.upsert(conn, chat_id, "meal", text, now=now)
        messenger.send(draft["question"] or "Can you tell me a bit more?")
        return

    for meal in draft["meals"]:
        diary.save_meal(conn, text, meal, now=now)
    messenger.send(format_ack(draft["meals"]))
    sessions.clear(conn, chat_id)


def _process_symptom(
    text: str,
    *,
    chat_id: str,
    llm: LLMClient,
    conn: sqlite3.Connection,
    messenger: Messenger,
    now: datetime | None = None,
) -> None:
    draft = structure_symptom(llm, text, now=now)

    if draft["needs_clarification"]:
        sessions.upsert(conn, chat_id, "symptom", text, now=now)
        messenger.send(draft["question"] or "Can you be more specific?")
        return

    for symptom in draft["symptoms"]:
        diary.save_symptom(conn, text, symptom, now=now)
    messenger.send(format_symptom_ack(draft["symptoms"]))
    sessions.clear(conn, chat_id)


def _process_note(
    text: str,
    *,
    diet_path: Path,
    messenger: Messenger,
    now: datetime | None = None,
) -> None:
    memory.append_note(diet_path, text, now=now)
    messenger.send("Got it — noted. I'll factor that into your weekly review.")


def dispatch(
    text: str,
    *,
    chat_id: str,
    llm: LLMClient,
    conn: sqlite3.Connection,
    messenger: Messenger,
    diet_path: Path,
    weekly_cb: Callable[[], None] | None = None,
    now: datetime | None = None,
) -> None:
    """Top-level entry: run the first-run interview while diet.md is empty,
    otherwise hand off to the normal intake pipeline."""
    command = text.strip()
    if command == "/clear-data":
        messenger.send(
            "⚠️ This erases ALL your data — meals, symptoms, profile, and my notes. "
            "Send '/clear-data confirm' to proceed."
        )
        return
    if command == "/clear-data confirm":
        admin.reset_all(conn, diet_path.parent)
        messenger.send("All data cleared. Send me anything to start fresh.")
        return
    if command == "/weekly" and weekly_cb is not None:
        messenger.send("Running your weekly review now…")
        weekly_cb()
        return

    if memory.is_empty(diet_path):
        _process_onboarding(
            text.strip(), chat_id=chat_id, llm=llm, conn=conn,
            messenger=messenger, diet_path=diet_path, now=now,
        )
        return
    handle_message(
        text, chat_id=chat_id, llm=llm, conn=conn, messenger=messenger,
        diet_path=diet_path, now=now,
    )


def _process_onboarding(
    text: str,
    *,
    chat_id: str,
    llm: LLMClient,
    conn: sqlite3.Connection,
    messenger: Messenger,
    diet_path: Path,
    now: datetime | None = None,
) -> None:
    session = sessions.get_active(conn, chat_id, now=now)
    prior = session.text if session and session.kind == "onboarding" else ""
    transcript = f"{prior}\nUser: {text}".strip()

    step = onboarding_step(llm, transcript)
    
    if not step["done"]:
        sessions.upsert(conn, chat_id, "onboarding", transcript + f"\nAssistant: {step['question']}", now=now)
        messenger.send(step["question"])
    else:
        memory.write_diet(diet_path, step["diet_markdown"], now=now)
        sessions.clear(conn, chat_id)
        messenger.send("Thanks — your profile is saved. You can start logging now.")