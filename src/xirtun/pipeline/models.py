"""Structured-output schemas, as Pydantic models.

Each model is passed to the LLM as the requested response shape; the provider emits
JSON matching it and we get a validated instance back. Fields with a default
(``= None`` or ``Field(default_factory=list)``) are optional.

The models are provider-agnostic — they describe what we want, not how a given LLM
produces it. Provider-specific handling lives in ``llm/gemini.py``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Intent(BaseModel):
    # Literal[...] limits the value to exactly these strings.
    intent: Literal["meal", "symptom", "note", "other"]


class Item(BaseModel):
    name: str
    quantity_g: float | None = None
    calories: float | None = None
    protein_g: float | None = None
    fat_g: float | None = None
    carbs_g: float | None = None
    tags: list[str] = Field(default_factory=list)


class MealEntry(BaseModel):
    """One eating occasion."""

    # ISO-8601 best estimate of WHEN the meal was eaten; null if the text gave no
    # time cue (the storage layer then falls back to the logging time).
    occurred_at: str | None = None
    items: list[Item]
    notes: str | None = None


class MealExtraction(BaseModel):
    """The structurer's output: either a clarifying question, or one-or-more meals.

    A single message can describe multiple eating occasions ("lunch X, dinner Y"),
    so `meals` is a list.
    """

    needs_clarification: bool = False
    question: str | None = None
    meals: list[MealEntry] = Field(default_factory=list)


class SymptomEntry(BaseModel):
    """One reported symptom occurrence."""

    occurred_at: str | None = None     # ISO-8601 estimate of when it occurred/started
    type: str                          # short lowercase label: "bloating", "headache"
    severity: int | None = None        # optional 1-5
    duration: str | None = None        # free text, e.g. "all morning"
    tags: list[str] = Field(default_factory=list)


class SymptomExtraction(BaseModel):
    """The symptom structurer's output: a clarifying question, or one-or-more symptoms."""

    needs_clarification: bool = False
    question: str | None = None
    symptoms: list[SymptomEntry] = Field(default_factory=list)


class OnboardingStep(BaseModel):
    """One step of the first-run interview: ask another question, or finish."""

    done: bool
    question: str | None = None        # set while done is False
    diet_markdown: str | None = None   # the finished profile, set when done is True


class AgentAction(BaseModel):
    """One step of the weekly agent loop: think, then either call a tool or finish."""

    thought: str
    tool: str | None = None              # tool name to call, or null to finish
    args_json: str = "{}"                # JSON object string of arguments for the tool
    final_message: str | None = None     # message to send the user when finishing (may be empty)
