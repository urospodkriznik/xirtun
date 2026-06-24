"""Parse a 'save this food' message into structured per-100g nutrition."""

from __future__ import annotations

from typing import Any

from xirtun.llm.base import LLMClient
from xirtun.pipeline.models import FoodRegistration

REGISTER_SYSTEM = (
    "Extract a single food's nutrition facts from the user's message into the schema. "
    "Nutrition values are PER 100g unless a different serving is clearly stated — "
    "convert to per-100g if so. Map common abbreviations: 'ch'/'carbs' -> carbohydrate, "
    "'protein(s)' -> protein; 'fibre'/'fibra' -> fiber_g. If a package/pack size in "
    "grams is mentioned, set package_g. Capture the brand if given. Do not "
    "invent numbers; leave a field null only if it truly isn't provided."
)


def parse_food(llm: LLMClient, text: str) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": REGISTER_SYSTEM},
        {"role": "user", "content": text},
    ]
    return llm.complete(messages, schema=FoodRegistration).data
