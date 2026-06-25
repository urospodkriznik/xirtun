"""The onboarding questionnaire, as versioned data.

Onboarding itself stays LLM-driven (the model does the asking and writes diet.md),
but *which* questions exist — and which old ones have been retired — is declared
here as a plain list. That gives us two things a prompt can't compute on its own:

  * a stable notion of an "onboarding version", so a profile can record which set
    of questions it was built from, and
  * a diff: when we add a question (or retire one), we can tell exactly what an
    older profile is missing and ask only that, instead of re-interviewing.

This mirrors the additive DB migrations in storage/db.py (``_migrate``): the schema
of *questions* evolves the same way the schema of *tables* does.

To evolve onboarding:
  * add a question     -> append to ONBOARDING_FIELDS with ``since`` = next version
  * retire a question  -> move it to DEPRECATED_FIELDS with ``removed_in`` = next version

You don't bump a version constant by hand — CURRENT_ONBOARDING_VERSION is derived
from the largest version mentioned below.
"""

from __future__ import annotations

from typing import Literal, TypedDict


class Field(TypedDict):
    key: str                                  # stable id; also referenced by DEPRECATED_FIELDS
    since: int                                # onboarding version that introduced this question
    target: Literal["profile", "metrics"]     # diet.md prose, or the structured metrics blob
    question: str                             # a suggested phrasing; the model may reword it


class Deprecated(TypedDict):
    key: str                                  # the retired field's key
    removed_in: int                           # onboarding version that retired it
    reason: str                               # why — fed to the model so its cleanup is informed


# The order is informative only; the model asks "one at a time" in whatever order
# feels natural. ``target`` says where an answer lands: free prose in diet.md, or
# the structured ``metrics`` blob that targets.py turns into calorie/protein goals.
ONBOARDING_FIELDS: list[Field] = [
    {"key": "sex",           "since": 1, "target": "metrics", "question": "What's your biological sex?"},
    {"key": "height_cm",     "since": 1, "target": "metrics", "question": "How tall are you?"},
    {"key": "weight_kg",     "since": 1, "target": "metrics", "question": "Roughly what do you weigh right now?"},
    {"key": "activity",      "since": 1, "target": "metrics", "question": "How active is a typical day for you?"},
    {"key": "allergies",     "since": 1, "target": "profile", "question": "Any food allergies or intolerances?"},
    {"key": "conditions",    "since": 1, "target": "profile", "question": "Any medical conditions I should know about?"},
    {"key": "family_history","since": 1, "target": "profile", "question": "Any relevant family medical history?"},
    {"key": "diet_style",    "since": 1, "target": "profile", "question": "How would you describe your diet, and any foods you avoid or love?"},
    {"key": "supplements",   "since": 1, "target": "profile", "question": "Do you take any supplements?"},
    {"key": "goals",         "since": 1, "target": "profile", "question": "What are your health or nutrition goals?"},
    # --- version 2 ---
    {"key": "residence",     "since": 2, "target": "profile", "question": "Where do you live for the majority of the year?"},
    # --- version 2 ---
    {"key": "birth_year",    "since": 3, "target": "metrics", "question": "What year were you born? (the month is a bonus)"},
]

# Questions we no longer ask. Kept here so a top-up can strip the now-stale answer
# from an older profile. Example: v1 recorded a raw age ("age: 35"); v2 derives age
# from ``birth_year`` instead (see targets.age_from), so a stored age is redundant
# and should be removed rather than left to rot.
DEPRECATED_FIELDS: list[Deprecated] = [
    {"key": "age", "removed_in": 2, "reason": "superseded by year of birth, which stays accurate as years pass"},
]


def _max_version() -> int:
    versions = [f["since"] for f in ONBOARDING_FIELDS]
    versions += [d["removed_in"] for d in DEPRECATED_FIELDS]
    return max(versions, default=1)


# The version a freshly onboarded profile satisfies. Bumps automatically when you
# add a field/deprecation at a higher number.
CURRENT_ONBOARDING_VERSION = _max_version()


def fields_since(version: int) -> list[Field]:
    """Questions introduced *after* ``version`` — what a top-up still needs to ask."""
    return [f for f in ONBOARDING_FIELDS if f["since"] > version]


def removed_since(version: int) -> list[Deprecated]:
    """Questions retired *after* ``version`` — what a top-up should strip from the profile."""
    return [d for d in DEPRECATED_FIELDS if d["removed_in"] > version]
