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
    intent: Literal["meal", "symptom", "exercise", "note", "shopping", "food", "other"]


class Item(BaseModel):
    name: str
    quantity_g: float | None = None
    calories: float | None = None
    protein_g: float | None = None
    fat_g: float | None = None
    carbs_g: float | None = None
    sugar_g: float | None = None       # sugars, a subset of carbs
    tags: list[str] = Field(default_factory=list)
    known_food: str | None = None      # name of a matching saved food, if any
    custom_meal: str | None = None     # name of a saved custom meal this item stands in for


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


class ExerciseEntry(BaseModel):
    """One physical-activity event."""

    occurred_at: str | None = None      # ISO-8601 estimate of when it happened
    type: str                           # short lowercase label: "running", "cycling"
    duration_min: float | None = None
    intensity: Literal["low", "moderate", "vigorous"] | None = None
    calories_burned: float | None = None   # estimated from type/duration/intensity/weight
    distance_km: float | None = None
    notes: str | None = None            # free text: sets/reps/weights/route
    tags: list[str] = Field(default_factory=list)


class ExerciseExtraction(BaseModel):
    """The exercise structurer's output: a clarifying question, or one-or-more activities."""

    needs_clarification: bool = False
    question: str | None = None
    exercises: list[ExerciseEntry] = Field(default_factory=list)


class Metrics(BaseModel):
    """Structured body metrics used to compute calorie/protein targets."""

    sex: Literal["male", "female", "other"] | None = None
    birth_year: int | None = None
    birth_month: int | None = None     # optional (1-12); refines the computed age
    height_cm: float | None = None
    weight_kg: float | None = None
    activity: Literal["sedentary", "light", "moderate", "active", "very_active"] | None = None
    activity_description: str | None = None  # what the user actually said; shown in /userinfo
    timezone: str | None = None        # IANA name (e.g. "Europe/Ljubljana"), inferred from residence


class ActivityClassification(BaseModel):
    """LLM output for /activity: map a free-text description to a standard level."""

    activity: Literal["sedentary", "light", "moderate", "active", "very_active"]
    explanation: str   # one sentence saying why this level fits


class OnboardingStep(BaseModel):
    """One step of the first-run interview: ask another question, or finish."""

    done: bool
    question: str | None = None        # set while done is False
    diet_markdown: str | None = None   # the finished profile, set when done is True
    metrics: Metrics | None = None     # structured body metrics, filled when done


class FoodRegistration(BaseModel):
    """A food's per-100g nutrition, parsed from a 'save this food' message."""

    name: str = Field(description="food name, including brand if given")
    brand: str | None = Field(default=None, description="brand, if mentioned")
    calories: float | None = Field(default=None, description="kcal per 100g")
    protein_g: float | None = Field(default=None, description="grams of protein per 100g")
    fat_g: float | None = Field(default=None, description="grams of fat per 100g")
    carbs_g: float | None = Field(
        default=None, description="grams of carbohydrate per 100g (also called 'carbs' or 'ch')"
    )
    sugar_g: float | None = Field(
        default=None,
        description="grams of sugars per 100g, a subset of carbs (often labelled 'of which sugars')",
    )
    fiber_g: float | None = Field(
        default=None, description="grams of fibre per 100g (also called 'fibre' or 'fibra')"
    )
    package_g: float | None = Field(default=None, description="total grams in one package, if stated")
    tags: list[str] = Field(default_factory=list)


class AgentAction(BaseModel):
    """One step of the weekly agent loop: think, then either call a tool or finish."""

    thought: str
    tool: str | None = None              # tool name to call, or null to finish
    args_json: str = "{}"                # JSON object string of arguments for the tool
    final_message: str | None = None     # message to send the user when finishing (may be empty)
    # Calibrating questions, set only when finishing — kept OUT of final_message so the
    # app can control send timing (hold for /weekly, follow up after a scheduled run).
    questions: list[str] = Field(default_factory=list)
