"""The first-run interview (and later top-ups) that produce diet.md.

Model-driven: given the conversation so far, it either asks the next question or,
once it has enough, returns the finished profile as Markdown. *Which* questions it
gathers is no longer hardcoded prose — it's passed in from the versioned registry
in onboarding_fields.py, so the same code drives both the full first-run interview
and a narrower top-up that only asks what an older profile is missing.
"""

from __future__ import annotations

from typing import Any

from xirtun.llm.base import LLMClient
from xirtun.pipeline.models import OnboardingStep
from xirtun.pipeline.onboarding_fields import Deprecated, Field

_METRICS_NOTE = (
    "Fill the `metrics` object with sex, year of birth (and month if known), height "
    "in cm, weight in kg, and activity level (sedentary/light/moderate/active/"
    "very_active) for any of those you learn. Keep everything factual; do not invent "
    "details. Respond using the provided schema."
)


def _format_fields(fields: list[Field]) -> str:
    return "\n".join(f"- {f['question']}" for f in fields)


def _full_system(fields: list[Field]) -> str:
    """Prompt for the first-run interview: gather everything, write a fresh profile."""
    return (
        "You are onboarding a new user of a personal nutrition assistant. Through a "
        "short, friendly interview — ONE question at a time — gather:\n"
        f"{_format_fields(fields)}\n\n"
        "You are given the conversation so far. If you still need important "
        "information, set done=false and ask the next single question. When you have "
        "enough for a useful profile, set done=true and put a concise, well-structured "
        "Markdown profile in diet_markdown (headed sections, bullet points; note where "
        "the user didn't say). "
        f"{_METRICS_NOTE}"
    )


def _topup_system(
    fields: list[Field], existing_profile: str, removals: list[Deprecated]
) -> str:
    """Prompt for a top-up: start from an existing profile, ask only the new
    questions, strip retired details, and return the merged profile."""
    if fields:
        asks = (
            "Ask ONLY about the new topics below, one question at a time:\n"
            f"{_format_fields(fields)}\n\n"
        )
    else:
        asks = "There are no new questions to ask; set done=true immediately.\n\n"

    remove = ""
    if removals:
        bullets = "\n".join(f"- {d['key']} ({d['reason']})" for d in removals)
        remove = (
            "Also REMOVE these now-redundant details from the profile if present, so "
            "they aren't left stale or duplicated:\n"
            f"{bullets}\n\n"
        )

    return (
        "You are updating the profile of an EXISTING user of a personal nutrition "
        "assistant. Their current profile is below; preserve everything in it except "
        "what you're explicitly told to remove.\n\n"
        f"--- current profile ---\n{existing_profile}\n--- end profile ---\n\n"
        f"{asks}{remove}"
        "Given the conversation so far: while you still need a new answer, set "
        "done=false and ask the next single question. When done, set done=true and put "
        "the FULL updated Markdown profile (existing content merged with the new "
        "answers, redundant details removed) in diet_markdown. "
        f"{_METRICS_NOTE}"
    )


def onboarding_step(
    llm: LLMClient,
    transcript: str,
    *,
    fields: list[Field],
    existing_profile: str | None = None,
    removals: list[Deprecated] | None = None,
) -> dict[str, Any]:
    """One step of the interview.

    ``existing_profile`` is None for the first-run interview and the current diet.md
    for a top-up — that switch is what selects the prompt.
    """
    if existing_profile is None:
        system = _full_system(fields)
    else:
        system = _topup_system(fields, existing_profile, removals or [])

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Conversation so far:\n{transcript}"},
    ]
    response = llm.complete(messages, schema=OnboardingStep)
    return response.data
