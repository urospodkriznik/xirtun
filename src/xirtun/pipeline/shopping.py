"""Shopping-list suggestions: a hot-path LLM reply, not a stored record.

Given the user's profile, recent meals, and recent symptoms, suggest a concise
grocery list of things they're likely missing. Uses the cheap model, returns text.
"""

from __future__ import annotations

from typing import Any

from xirtun.llm.base import LLMClient

SHOPPING_SYSTEM = (
    "You are a personal nutrition assistant helping plan grocery shopping. Using the "
    "user's profile (goals, diet style, allergies, conditions), the meals they've "
    "eaten this past week, and any symptoms, suggest a SHORT list of items they are "
    "likely MISSING.\n"
    "- Do NOT suggest foods they've already eaten recently — they don't need more.\n"
    "- Favour items that fill gaps for their goals, and avoid likely symptom triggers.\n"
    "- Respect allergies and diet style strictly.\n"
    "- Be concise: group by category, at most a few words of reason per item. No "
    "preamble, no medical advice."
)


def _format_meals(meals: list[dict[str, Any]]) -> str:
    if not meals:
        return "(nothing logged)"
    return "\n".join(
        f"- {meal['occurred_at'][:10]}: " + ", ".join(item["name"] for item in meal["items"])
        for meal in meals
    )


def _format_symptoms(symptoms: list[dict[str, Any]]) -> str:
    if not symptoms:
        return "(none)"
    return "\n".join(f"- {s['occurred_at'][:10]}: {s['type']}" for s in symptoms)


def suggest_shopping(
    llm: LLMClient,
    *,
    profile: str,
    recent_meals: list[dict[str, Any]],
    recent_symptoms: list[dict[str, Any]],
    request: str,
) -> str:
    user = (
        f"My profile:\n{profile or '(none yet)'}\n\n"
        f"Meals I've eaten this past week:\n{_format_meals(recent_meals)}\n\n"
        f"Symptoms this past week:\n{_format_symptoms(recent_symptoms)}\n\n"
        f"My request: {request}"
    )
    messages = [
        {"role": "system", "content": SHOPPING_SYSTEM},
        {"role": "user", "content": user},
    ]
    return llm.complete(messages).text
