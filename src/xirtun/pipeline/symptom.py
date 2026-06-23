"""Turn a symptom report into structured symptom event(s) or a follow-up question.

Mirrors structure.py (meals): same occurred_at inference, same multi-entry +
clarification shape — symptoms just carry different fields.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from xirtun.llm.base import LLMClient
from xirtun.pipeline.models import SymptomExtraction

# Instructs the model to structure one or more reported symptoms, infer when each
# occurred, and ask for clarification when a report is too vague.
SYMPTOM_SYSTEM = (
    "You convert a user's report of how they feel into structured symptom events "
    "for a personal health assistant that looks for links between food and "
    "symptoms.\n"
    "A message may mention MULTIPLE symptoms; return one entry per symptom.\n"
    "If a report is too vague to be useful, set needs_clarification=true and ask "
    "ONE short question; leave symptoms empty. Otherwise set "
    "needs_clarification=false and fill symptoms.\n"
    "For each symptom: a short lowercase `type` ('bloating', 'headache', "
    "'fatigue', 'nausea'); severity 1-5 if expressed or clearly inferable, else "
    "null; duration as free text if given ('all morning', '2 hours'), else null; "
    "and tags for notable qualifiers.\n"
    "Set occurred_at to your best ISO-8601 estimate of WHEN the symptom "
    "occurred/started, using the current date/time and cues ('this morning', "
    "'after lunch', 'right now'); express it with the same UTC offset as the "
    "current time. If there's no cue, leave occurred_at null.\n"
    "Do NOT diagnose or speculate about causes. Respond using the provided schema."
)


def structure_symptom(llm: LLMClient, text: str, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now().astimezone()
    user = (
        f"Current date and time: {now:%Y-%m-%d %H:%M %A} ({now:%Z}, UTC{now:%z}).\n\n"
        f"How I feel:\n{text}"
    )
    messages = [
        {"role": "system", "content": SYMPTOM_SYSTEM},
        {"role": "user", "content": user},
    ]
    response = llm.complete(messages, schema=SymptomExtraction)
    return response.data
