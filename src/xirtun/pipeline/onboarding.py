"""The first-run interview that produces diet.md.

Model-driven: given the conversation so far, it either asks the next question or,
once it has enough, returns the finished profile as Markdown.
"""

from __future__ import annotations

from typing import Any

from xirtun.llm.base import LLMClient
from xirtun.pipeline.models import OnboardingStep

ONBOARDING_SYSTEM = (
    "You are onboarding a new user of a personal nutrition assistant. Through a "
    "short, friendly interview — ONE question at a time — gather: sex, year of birth "
    "(and month if offered), height, weight, and typical activity level; food "
    "allergies and intolerances; medical "
    "conditions; relevant family medical history; diet style and food "
    "preferences/dislikes; supplements taken; and health/nutrition goals.\n"
    "You are given the conversation so far. If you still need important "
    "information, set done=false and ask the next single question. When you have "
    "enough for a useful profile, set done=true and put a concise, well-structured "
    "Markdown profile in diet_markdown (headed sections, bullet points; note where "
    "the user didn't say), and fill the `metrics` object with their sex, year of "
    "birth (and month if known), height in cm, weight in kg, and activity level "
    "(sedentary/light/moderate/active/"
    "very_active) where known. Keep it factual; do not invent details. Respond using "
    "the provided schema."
)


def onboarding_step(llm: LLMClient, transcript: str) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": ONBOARDING_SYSTEM},
        {"role": "user", "content": f"Conversation so far:\n{transcript}"},
    ]
    response = llm.complete(messages, schema=OnboardingStep)
    return response.data
