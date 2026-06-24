"""Structure an activity report into one-or-more exercise events, or a follow-up question.

Mirrors symptom.py: same occurred_at inference, multi-entry, and clarification shape.
The user's body weight is provided so the model can estimate calories burned.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from xirtun.llm.base import LLMClient
from xirtun.pipeline.models import ExerciseExtraction

EXERCISE_SYSTEM = (
    "You convert a description of physical activity into structured exercise events "
    "for a personal health assistant.\n"
    "A message may describe MULTIPLE activities; return one entry per activity.\n"
    "If a report is too vague to be useful, set needs_clarification=true and ask ONE "
    "short question; leave exercises empty. Otherwise set needs_clarification=false "
    "and fill exercises.\n"
    "For each: a short lowercase `type` ('running', 'weight training', 'cycling', "
    "'yoga'); duration_min in minutes if given; intensity as low/moderate/vigorous; "
    "distance_km if relevant; notes for specifics (sets/reps/weights/route); and tags "
    "('cardio', 'strength', 'legs').\n"
    "Estimate calories_burned from the activity, duration, intensity, and the user's "
    "body weight (provided). Estimates are rough (±20-30% is fine).\n"
    "Set occurred_at to your best ISO-8601 estimate of WHEN it happened, using the "
    "current date/time and cues ('this morning', 'yesterday'); express it with the "
    "same UTC offset as the current time. If there's no cue, leave occurred_at null.\n"
    "Respond using the provided schema."
)


def structure_exercise(
    llm: LLMClient,
    text: str,
    *,
    now: datetime | None = None,
    weight_kg: float | None = None,
) -> dict[str, Any]:
    now = now or datetime.now().astimezone()
    user = (
        f"Current date and time: {now:%Y-%m-%d %H:%M %A} ({now:%Z}, UTC{now:%z}).\n"
        f"My body weight: {f'{weight_kg}kg' if weight_kg else 'unknown'}.\n\n"
        f"My activity:\n{text}"
    )
    messages = [
        {"role": "system", "content": EXERCISE_SYSTEM},
        {"role": "user", "content": user},
    ]
    return llm.complete(messages, schema=ExerciseExtraction).data
