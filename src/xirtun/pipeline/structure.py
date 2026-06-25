"""Turn a meal description into either a follow-up question or one-or-more meals.

The structurer is told the current date/time so it can estimate WHEN each meal was
eaten from cues in the text ("lunch", "this morning", "yesterday"). A single message
may describe several eating occasions, so it returns a list of meals.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from xirtun.llm.base import LLMClient
from xirtun.pipeline.models import MealExtraction

# Instructs the model to decompose meals into ingredients, estimate nutrition, tag
# allergens/sensitivities, infer when each meal occurred, and ask for clarification
# when a description is too vague to estimate.
STRUCTURE_SYSTEM = (
    "You convert a description of what someone ate into structured, ESTIMATED "
    "nutrition for a personal health assistant that looks for links between food "
    "and symptoms.\n"
    "A single message may describe MULTIPLE eating occasions at different times "
    "(e.g. 'for lunch I had X, for dinner Y'). Return one entry in `meals` per "
    "occasion.\n"
    "If the description is too vague to estimate reasonably (missing portion size, "
    "unclear preparation, or unclear contents), set needs_clarification=true, ask "
    "ONE short specific question, and leave meals empty. Otherwise set "
    "needs_clarification=false and fill meals.\n"
    "For each meal, set occurred_at to your best ISO-8601 estimate of WHEN it was "
    "eaten, using the current date/time provided and any cue in the text ('lunch', "
    "'this morning', 'yesterday'). If there is no time cue, leave occurred_at null.\n"
    "For each meal's items:\n"
    "- Break composite foods into likely component ingredients when it matters for "
    "allergen/sensitivity tracking (a sandwich -> bread, chicken, mayo, lettuce).\n"
    "- For each item: name, quantity in grams, calories, protein/fat/carbs in grams.\n"
    "- If an item matches one of the user's known foods (listed in the message), set "
    "`known_food` to its exact name and include quantity_g — its nutrition will be "
    "filled from the saved label.\n"
    "- If the user refers to one of their saved custom meals (listed in the message), "
    "represent it as a SINGLE item with `custom_meal` set to its exact name and do "
    "NOT itemize it — it will be expanded from the saved recipe.\n"
    "- Tag each item with likely SENSITIVITY/ALLERGEN markers (dairy, gluten, soy, "
    "egg, nuts, shellfish, nightshade, histamine, caffeine, alcohol, fodmap) plus "
    "notable attributes ('iron-rich', 'fried', 'processed').\n"
    "Estimates are rough (±20-30% is fine). Ask at most one question at a time. "
    "Respond using the provided schema."
)


def structure_meal(
    llm: LLMClient,
    text: str,
    *,
    now: datetime | None = None,
    known_foods: list[dict[str, Any]] | None = None,
    custom_meal_names: list[str] | None = None,
) -> dict[str, Any]:
    now = now or datetime.now().astimezone()
    known = ""
    if known_foods:
        lines = []
        for food in known_foods:
            line = f"- {food['name']}"
            if food.get("package_g"):
                line += f" (whole package = {round(food['package_g'])}g)"
            lines.append(line)
        known = "\n\nMy known foods (set known_food to the exact name shown):\n" + "\n".join(lines)
    if custom_meal_names:
        known += (
            "\n\nMy saved custom meals (if I say I ate one, set `custom_meal` to its "
            "exact name as a single item; don't itemize it):\n"
            + "\n".join(f"- {name}" for name in custom_meal_names)
        )
    user = (
        f"Current date and time: {now:%Y-%m-%d %H:%M %A} ({now:%Z}, UTC{now:%z}).\n\n"
        f"What I ate:\n{text}{known}"
    )
    messages = [
        {"role": "system", "content": STRUCTURE_SYSTEM},
        {"role": "user", "content": user},
    ]
    response = llm.complete(messages, schema=MealExtraction)
    return response.data
