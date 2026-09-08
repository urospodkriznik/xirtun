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
from xirtun.storage import custom_meals

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
    "- For each item: name, quantity in grams, calories, protein/fat/carbs in grams, "
    "plus sugars (a subset of carbs) and dietary fibre in grams when they can be "
    "reasonably estimated, and sodium as `sodium_mg` in MILLIgrams — most sodium comes "
    "from processed foods, bread, cheese, sauces and salt added in cooking, so estimate "
    "it from how the food was made rather than defaulting to zero.\n"
    "- Set `known_food` ONLY when the description clearly and specifically identifies "
    "that exact saved product — matching brand, or a distinctive ingredient the saved "
    "name calls out (e.g. 'chickpea pasta' matches a saved 'chickpea pasta', but plain "
    "'pasta' or 'spinach pasta' does NOT match a saved 'chickpea pasta' just because "
    "both contain the word 'pasta'). A shared generic word is NOT a match. When unsure, "
    "leave known_food null and estimate the ingredient generically instead — a rough "
    "estimate is better than applying the wrong saved label. When it does match, still "
    "include quantity_g — its nutrition will be filled from the saved label.\n"
    "- If the user refers to one of their saved custom meals (listed in the message), "
    "represent it as a SINGLE item with `custom_meal` set to its exact name and do "
    "NOT itemize it — it will be expanded from the saved recipe. If they ate only part "
    "of it, set `portion` to the fraction eaten (0.5 for 'half', 0.67 for 'two thirds', "
    "2 for 'a double portion'); leave it null for a full/normal portion.\n"
    "- Set `custom_meal` ONLY when the user has NAMED that saved meal — every "
    "significant word of the saved name should be there in what they wrote. A shared "
    "generic word is NOT a match: 'oat cereals' does NOT mean a saved 'breakfast "
    "cereals', and a typo you can't read ('iat cereals') is not a licence to reach for "
    "the nearest saved name. Expanding a recipe adds SEVERAL foods at once, so a wrong "
    "match doesn't just mis-estimate one item, it logs a meal the user never ate — "
    "when unsure, leave `custom_meal` null and estimate the ingredients normally.\n"
    "- Never do both: if you set `custom_meal`, don't ALSO list that recipe's "
    "ingredients (or the user's own words for them) as separate items, or everything "
    "in it gets counted twice.\n"
    "- If the user NAMES a saved meal but CHANGES it — leaves an ingredient out, swaps "
    "one, or alters an amount ('breakfast cereals but with less milk (0.4l), without "
    "musli and additional banana') — do NOT set `custom_meal`. Itemize the adjusted "
    "meal yourself: start from the saved contents shown below the meal list, drop what "
    "they removed, use their amount where they gave one, and add what they added. "
    "Setting `custom_meal` there logs the saved version unchanged, so the removed "
    "ingredient still gets counted and their replacement amount is added on top of the "
    "original one. `portion` scales the WHOLE meal ('half my breakfast cereals') and "
    "cannot express a change to a single ingredient.\n"
    "- Tag each item with likely SENSITIVITY/ALLERGEN markers (dairy, gluten, soy, "
    "egg, nuts, shellfish, nightshade, histamine, caffeine, alcohol, fodmap) plus "
    "notable attributes ('iron-rich', 'fried', 'processed').\n"
    # Measured, not guessed: without these lines the cheap model collapsed stated
    # multiples back toward one serving — "3 portions of pasta" came out 1.35x "a
    # portion of pasta" (150g vs 225g). With them the same model scales it exactly 3x
    # (150g -> 450g), matching the strong model, so this stays a prompt fix rather
    # than an upgrade to a pricier model on every meal.
    "QUANTITY IS THE NUMBER THAT MATTERS MOST — get it right before the macros.\n"
    "- quantity_g is the TOTAL amount eaten of that item, never a per-portion amount.\n"
    "- When the user states a COUNT of portions/servings/plates/bowls ('two portions', "
    "'3 portions', 'a double helping'), multiply a single serving by that count. Two "
    "portions is TWICE one portion; three is THREE TIMES. Never collapse a stated "
    "multiple back toward a single serving.\n"
    "- Size words scale further: 'big'/'large' ~1.5x a normal serving, 'very big'/'huge' "
    "~2x, 'small' ~0.6x. Apply the size word FIRST, then the count.\n"
    "- Only scale what the count refers to: in 'three portions of pasta with a small "
    "glass of beer', the beer is still one small glass.\n"
    "- When the user emphasises the amount ('it really was a lot', 'I was stuffed'), take "
    "them literally — err on the HIGH side, not the safe middle.\n"
    "- Sanity-check before answering: would this quantity plausibly leave that person "
    "full? Restating a big meal as one modest serving is the most damaging error you can "
    "make here, because it silently understates every total that follows.\n"
    "Estimates are rough (±20-30% is fine), but that tolerance is for the nutrition of a "
    "known amount — it is NOT licence to guess the amount itself. Ask at most one "
    "question at a time. Respond using the provided schema."
)


def structure_meal(
    llm: LLMClient,
    text: str,
    *,
    now: datetime | None = None,
    known_foods: list[dict[str, Any]] | None = None,
    saved_meals: list[dict[str, Any]] | None = None,
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
    if saved_meals:
        known += (
            "\n\nMy saved custom meals (if I say I ate one, set `custom_meal` to its "
            "exact name as a single item; don't itemize it):\n"
            + "\n".join(f"- {m['name']}" for m in saved_meals)
        )
        # Spell out the contents of any saved meal the message actually names — the
        # model can't honour "without the muesli" while it only knows the meal's name.
        # Only the named ones, so a long recipe book doesn't ride along on every log.
        named = [m for m in saved_meals if custom_meals.name_supported_by(m["name"], text)]
        if named:
            known += (
                "\n\nWhat's in the saved meal(s) I seem to be naming — use this if I "
                "changed anything about them:\n"
                + "\n".join(
                    f"- {m['name']} = " + ", ".join(
                        f"{i.get('name')} {round(i['quantity_g'])}g" if i.get("quantity_g")
                        else str(i.get("name"))
                        for i in (m.get("items") or [])
                    )
                    for m in named
                )
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
    data = response.data

    # Two ways this output can be wrong in a manner no later step can detect, both worth
    # one cheap corrective call: macros that can't produce their own calorie figure
    # (usually a portion size in a macro field), and a saved meal matched to words the
    # user never wrote (which silently adds a whole recipe). If the retry is no better we
    # keep what we have rather than block the user's log on it.
    if _problems(data, text):
        retry = llm.complete(
            messages + [{"role": "user", "content": _correction_request(data, text)}],
            schema=MealExtraction,
        )
        if retry.data and not _problems(retry.data, text):
            return retry.data
    return data


def _problems(data: dict[str, Any] | None, text: str) -> list[str]:
    return impossible_items(data) + unnamed_custom_meals(data, text) + altered_custom_meals(data, text)


# Words that signal a saved meal was changed in a way `portion` can't express. Only
# removals and amount changes: an ADDITION ("cereals and a banana") is representable —
# the recipe plus a separate item — so it isn't worth a retry.
_ALTERATION_WORDS = ("without", "minus", "skip", "instead of", "leave out", "less")


def altered_custom_meals(data: dict[str, Any] | None, text: str) -> list[str]:
    """Saved meals the model marked for wholesale expansion while the user was plainly
    changing them. Expanding there logs the ingredient they removed and double-counts
    the one they re-specified."""
    if not data:
        return []
    lowered = text.lower()
    if not any(word in lowered for word in _ALTERATION_WORDS):
        return []
    return [
        name for meal in (data.get("meals") or [])
        for item in (meal.get("items") or [])
        if (name := item.get("custom_meal"))
    ]


def _correction_request(data: dict[str, Any] | None, text: str) -> str:
    parts = []
    if impossible := impossible_items(data):
        parts.append(
            "Your estimate is arithmetically impossible for: " + "; ".join(impossible)
            + ". Protein and carbs are 4 kcal per gram and fat is 9, so those macros "
            "imply far more energy than the calorie figure you gave. Check whether a "
            "portion size ended up in a macro field."
        )
    if unnamed := unnamed_custom_meals(data, text):
        parts.append(
            "You matched saved meal(s) the user did not name: " + "; ".join(unnamed)
            + ". A shared generic word is not a match, and neither is a typo that "
            "resembles one. Estimate those ingredients normally instead, and don't list "
            "a recipe's contents twice."
        )
    if altered := altered_custom_meals(data, text):
        parts.append(
            "The user is CHANGING the saved meal(s) they named: " + "; ".join(altered)
            + ". Setting `custom_meal` logs the saved version unchanged, so what they "
            "removed still counts and what they re-specified is added on top. Drop "
            "`custom_meal` and itemize the adjusted meal from the saved contents shown."
        )
    return " ".join(parts) + " Re-estimate the whole message, keeping everything else the same."


def unnamed_custom_meals(data: dict[str, Any] | None, text: str) -> list[str]:
    """Saved meals the model matched that the user's own words don't support.

    Expanding a saved meal swaps one word for a whole recipe, so this error adds food
    that was never eaten — and it looks entirely plausible in the diary afterwards.
    """
    if not data:
        return []
    return [
        name for meal in (data.get("meals") or [])
        for item in (meal.get("items") or [])
        if (name := item.get("custom_meal")) and not custom_meals.name_supported_by(name, text)
    ]


def impossible_items(data: dict[str, Any] | None) -> list[str]:
    """Names of items whose macros imply far more energy than their calorie figure.

    Same test the deep-dive export applies when reading old rows — applied here so the
    row is questioned before it is stored, rather than explained after the fact.
    """
    if not data:
        return []
    bad = []
    for meal in data.get("meals") or []:
        for item in meal.get("items") or []:
            stated = item.get("calories")
            if not stated:
                continue
            implied = (
                (item.get("protein_g") or 0) * 4
                + (item.get("fat_g") or 0) * 9
                + (item.get("carbs_g") or 0) * 4
            )
            if implied > stated * 1.5 + 100:
                bad.append(f"{item.get('name')} ({round(stated)} kcal but macros imply {round(implied)})")
    return bad
