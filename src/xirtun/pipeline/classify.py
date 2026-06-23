"""Classify an inbound message into an intent: meal | symptom | other."""

from __future__ import annotations

from xirtun.llm.base import LLMClient
from xirtun.pipeline.models import Intent

CLASSIFY_SYSTEM = (
    "You triage messages sent to a personal nutrition assistant. "
    "Classify the user's message into exactly one intent:\n"
    "- 'meal': they are describing food or drink they consumed.\n"
    "- 'symptom': they are reporting a physical symptom or how they feel.\n"
    "- 'note': they are sharing a goal, preference, routine, exercise habit, or a "
    "fact/idea to remember (e.g. 'I want to gain muscle', 'I exercise twice a week', "
    "'I want more lutein in my diet').\n"
    "- 'shopping': they are asking what to buy or for help planning groceries.\n"
    "- 'other': anything else (questions, chit-chat).\n"
    "Respond using the provided schema."
)


def classify(llm: LLMClient, text: str) -> str:
    messages = [
        {"role": "system", "content": CLASSIFY_SYSTEM},
        {"role": "user", "content": text},
    ]
    response = llm.complete(messages, schema=Intent)
    return response.data["intent"]
