"""The hot-path orchestrator — the reactive state machine from docs/architecture.md.

Handles meals and symptoms through one shape: classify -> (clarify?) -> structure
-> store. A single message may describe several meals or symptoms, each stored as
its own row (separate-events model).

Sessions remember whether we're mid-MEAL or mid-SYMPTOM (session.kind), so a
follow-up answer is routed to the right processor.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from xirtun.llm.base import LLMClient
from xirtun.messaging.base import Messenger
from xirtun.memory import diet as memory
from xirtun.pipeline import sessions
from xirtun.pipeline.classify import classify
from xirtun.pipeline.food import parse_food
from xirtun.pipeline.onboarding import onboarding_step
from xirtun.pipeline.shopping import suggest_shopping
from xirtun.pipeline.structure import structure_meal
from xirtun.pipeline.symptom import structure_symptom
from xirtun import export, reports, targets
from xirtun.storage import admin, diary, foods

logger = logging.getLogger(__name__)

MEAL_COMMANDS = {"/meal", "/new"}

HELP_TEXT = (
    "Just tell me what you ate or how you feel, in plain language, and I'll log it. "
    "You can also share goals or notes (e.g. 'I want to gain muscle').\n\n"
    "Commands:\n"
    "/meal — start a new meal entry\n"
    "/undo — remove your last entry (asks to confirm)\n"
    "/today — today's meals and totals\n"
    "/week — the past 7 days\n"
    "/shop — suggest a shopping list\n"
    "/food <name>: <per-100g nutrition> — save a food's label\n"
    "/myfood — list your saved foods\n"
    "/checkfood <name> — check if a food is saved\n"
    "/delfood <name> — remove a saved food\n"
    "/target — your daily calorie & protein target\n"
    "/weight <kg> — update your weight\n"
    "/export — download your diary as a JSON backup\n"
    "/profile — show your profile\n"
    "/weekly — run your weekly review now\n"
    "/cleardata — erase everything (asks to confirm)"
)


def format_ack(meals: list[dict[str, Any]]) -> str:
    """Confirmation summarizing the meal(s) logged, with per-item and total macros."""
    item_lines = []
    totals = {"calories": 0.0, "protein_g": 0.0, "fat_g": 0.0, "carbs_g": 0.0}
    for meal in meals:
        for item in meal["items"]:
            for key in totals:
                totals[key] += item.get(key) or 0
            item_lines.append(
                f"- {item['name']} (~{round(item.get('calories') or 0)} kcal, "
                f"{round(item.get('protein_g') or 0)}g protein)"
            )
    header = "Logged:" if len(meals) == 1 else f"Logged {len(meals)} meals:"
    total = (
        f"Total: ~{round(totals['calories'])} kcal — {round(totals['protein_g'])}g protein, "
        f"{round(totals['fat_g'])}g fat, {round(totals['carbs_g'])}g carbs."
    )
    return f"{header}\n" + "\n".join(item_lines) + f"\n{total}"


def format_symptom_ack(symptoms: list[dict[str, Any]]) -> str:
    """Confirmation summarizing the symptom(s) just logged."""
    parts = []
    for s in symptoms:
        extra = []
        if s.get("severity"):
            extra.append(f"severity {s['severity']}/5")
        if s.get("duration"):
            extra.append(str(s["duration"]))
        suffix = f" ({', '.join(extra)})" if extra else ""
        parts.append(f"{s['type']}{suffix}")
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
        entry = diary.last_entry(conn)
        if entry is None:
            messenger.send("Nothing to undo.")
        else:
            sessions.upsert(conn, chat_id, "undo_confirm", json.dumps(entry), now=now)
            messenger.send(
                f"This will remove your last entry — {entry['description']}.\n"
                "Reply 'yes' to confirm, or anything else to cancel."
            )
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
    if text == "/shop":
        _process_shopping(
            "What should I buy this week?",
            conn=conn, diet_path=diet_path, llm=llm, messenger=messenger, now=now,
        )
        return
    if text == "/myfood":
        rows = foods.all_rows(conn)
        if rows:
            messenger.send("Your saved foods:\n" + "\n".join("- " + _food_line(r) for r in rows))
        else:
            messenger.send("You haven't saved any foods yet. Use /food to add one.")
        return
    if text.startswith("/checkfood"):
        query = text[len("/checkfood"):].strip()
        messenger.send(_food_lookup(conn, query) if query else "Usage: /checkfood <name>")
        return
    if text.startswith("/delfood"):
        name = text[len("/delfood"):].strip()
        if not name:
            messenger.send("Usage: /delfood <name>")
        elif foods.delete(conn, name):
            messenger.send(f"Removed '{name}' from your saved foods.")
        else:
            messenger.send(f"No saved food named '{name}'.")
        return
    if text.startswith("/food"):
        payload = text[len("/food"):].strip()
        if payload:
            _process_food(payload, chat_id=chat_id, conn=conn, llm=llm, messenger=messenger, now=now)
        else:
            messenger.send(
                "Usage: /food <name>: per 100g — e.g. /food Lidl vegan sausage: "
                "250 kcal, 18g protein, 12g fat, 4g carbs"
            )
        return
    if text == "/target":
        messenger.send(targets.format_targets(targets.read_metrics(conn)))
        return
    if text.startswith("/weight"):
        parts = text.split()
        try:
            targets.update_weight(conn, float(parts[1]))
            messenger.send(f"Updated your weight to {float(parts[1]):g} kg.")
        except (IndexError, ValueError):
            messenger.send("Usage: /weight 75")
        return
    if text == "/export":
        messenger.send_document(
            export.export_filename(now),
            export.export_json(conn, now=now),
            caption="Your diary export (meals, symptoms, and saved foods).",
        )
        return

    # 2) Mid-session: continue whatever we were collecting (meal or symptom).
    session = sessions.get_active(conn, chat_id, now=now)
    if session is not None:
        if session.kind == "food_confirm":
            _resolve_food_confirm(session, text, chat_id=chat_id, conn=conn, messenger=messenger)
            return
        if session.kind == "undo_confirm":
            _resolve_undo(session, text, chat_id=chat_id, conn=conn, messenger=messenger)
            return
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
    elif intent == "shopping":
        _process_shopping(text, conn=conn, diet_path=diet_path, llm=llm, messenger=messenger, now=now)
    elif intent == "food":
        _process_food(text, chat_id=chat_id, conn=conn, llm=llm, messenger=messenger, now=now)
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
    draft = structure_meal(llm, text, now=now, known_foods=foods.for_prompt(conn))

    if draft["needs_clarification"]:
        sessions.upsert(conn, chat_id, "meal", text, now=now)
        messenger.send(draft["question"] or "Can you tell me a bit more?")
        return

    for meal in draft["meals"]:
        _apply_known_foods(conn, meal)
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


def _process_shopping(
    text: str,
    *,
    conn: sqlite3.Connection,
    diet_path: Path | None,
    llm: LLMClient,
    messenger: Messenger,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now().astimezone()
    since = (now - timedelta(days=7)).isoformat()
    profile = memory.read_diet(diet_path) if diet_path else ""
    recent_meals = diary.meals_since(conn, since)
    recent_symptoms = diary.symptoms_since(conn, since)
    messenger.send(
        suggest_shopping(
            llm,
            profile=profile,
            recent_meals=recent_meals,
            recent_symptoms=recent_symptoms,
            request=text,
        )
    )


def _process_food(
    text: str,
    *,
    chat_id: str,
    conn: sqlite3.Connection,
    llm: LLMClient,
    messenger: Messenger,
    now: datetime | None = None,
) -> None:
    food = parse_food(llm, text)
    if not food.get("name") or food.get("calories") is None:
        messenger.send(
            "I couldn't read that food. Try e.g. 'save Lidl vegan sausage: per 100g — "
            "250 kcal, 18g protein, 12g fat, 4g carbs'."
        )
        return

    # Same name already saved -> just update it, no need to ask.
    if foods.find_by_name(conn, food["name"]):
        foods.add(conn, food)
        messenger.send("Updated — " + _food_line(food))
        return

    # A similarly-named food exists -> ask whether it's the same item.
    similar = foods.search(conn, food["name"])
    if similar:
        sessions.upsert(
            conn, chat_id, "food_confirm",
            json.dumps({"food": food, "candidate": similar[0]}), now=now,
        )
        messenger.send(
            f"You already have '{similar[0]}'. Reply 'update' to overwrite it, 'add' to "
            f"save '{food['name']}' as a new entry, or 'cancel'."
        )
        return

    _save_food(conn, food, messenger)


def _save_food(conn: sqlite3.Connection, food: dict[str, Any], messenger: Messenger) -> None:
    foods.add(conn, food)
    messenger.send("Saved — " + _food_line(food) + "\nI'll use it whenever you log it.")


def _resolve_food_confirm(
    session,
    answer: str,
    *,
    chat_id: str,
    conn: sqlite3.Connection,
    messenger: Messenger,
) -> None:
    data = json.loads(session.text)
    food, candidate = data["food"], data["candidate"]
    sessions.clear(conn, chat_id)
    choice = answer.strip().lower()
    if choice.startswith(("u", "y")):       # update / overwrite the existing food
        merged = {**food, "name": candidate}
        foods.add(conn, merged)
        messenger.send("Updated — " + _food_line(merged))
    elif choice.startswith("a"):            # add as a new, separate food
        _save_food(conn, food, messenger)
    else:                                    # cancel (default for anything unrecognized)
        messenger.send("Cancelled. Nothing was saved.")


def _resolve_undo(
    session,
    answer: str,
    *,
    chat_id: str,
    conn: sqlite3.Connection,
    messenger: Messenger,
) -> None:
    entry = json.loads(session.text)
    sessions.clear(conn, chat_id)
    if answer.strip().lower().startswith("y"):
        diary.delete_entry(conn, entry["kind"], entry["id"])
        messenger.send(f"Removed — {entry['description']}.")
    else:
        messenger.send("Cancelled. Nothing was removed.")


def _food_macro(value: float | None) -> int:
    return round(value) if value is not None else 0


def _food_line(food: dict[str, Any]) -> str:
    parts = [f"{food['name']} — {_food_macro(food.get('calories'))} kcal/100g"]
    macros = (
        f"P{_food_macro(food.get('protein_g'))} "
        f"F{_food_macro(food.get('fat_g'))} "
        f"C{_food_macro(food.get('carbs_g'))}"
    )
    if food.get("fiber_g") is not None:
        macros += f" Fb{_food_macro(food.get('fiber_g'))}"
    parts.append(f"({macros})")
    if food.get("package_g"):
        parts.append(f"[{round(food['package_g'])}g/pack]")
    return " ".join(parts)


def _food_lookup(conn: sqlite3.Connection, query: str) -> str:
    names = []
    exact = foods.find_by_name(conn, query)
    if exact:
        names.append(exact["name"])
    names += [name for name in foods.search(conn, query) if name not in names]
    if not names:
        return f"No saved food matching '{query}'."
    return "Matches:\n" + "\n".join("- " + _food_line(foods.find_by_name(conn, name)) for name in names)


def _apply_known_foods(conn: sqlite3.Connection, meal: dict[str, Any]) -> None:
    """Replace estimated macros with exact saved label values for matched foods."""
    for item in meal["items"]:
        name = item.get("known_food")
        quantity = item.get("quantity_g")
        if not name or quantity is None:
            continue
        food = foods.find_by_name(conn, name) or foods.find_by_name(conn, name.split("(")[0].strip())
        if food is None:
            continue
        factor = quantity / 100.0
        for key in ("calories", "protein_g", "fat_g", "carbs_g"):
            if food.get(key) is not None:
                item[key] = round(food[key] * factor, 1)


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
    if command == "/cleardata":
        messenger.send(
            "⚠️ This erases ALL your data — meals, symptoms, profile, and my notes. "
            "Send '/cleardata confirm' to proceed."
        )
        return
    if command == "/cleardata confirm":
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
        if step.get("metrics"):
            targets.write_metrics(conn, step["metrics"])
        sessions.clear(conn, chat_id)
        messenger.send("Thanks — your profile is saved. You can start logging now.")