"""Tests for saved custom meals (recipes)."""

from xirtun.storage import custom_meals


def test_add_find_and_totals(conn):
    items = [
        {"name": "muesli", "calories": 280, "protein_g": 8},
        {"name": "oat milk", "calories": 120, "protein_g": 3},
    ]
    custom_meals.add(conn, "Breakfast", items)

    cm = custom_meals.find_by_name(conn, "breakfast")  # case-insensitive
    assert cm is not None
    assert cm["calories"] == 400
    assert round(cm["protein_g"]) == 11
    assert len(cm["items"]) == 2


def test_delete(conn):
    custom_meals.add(conn, "Breakfast", [{"name": "x", "calories": 100}])
    assert custom_meals.delete(conn, "breakfast") is True
    assert custom_meals.delete(conn, "breakfast") is False


# --- guarding the expansion match ---

def test_saved_meal_is_used_only_when_the_user_named_it():
    """From the live diary: "iat cereals" (a typo for "oat cereals") pulled in the whole
    saved "breakfast cereals" recipe — muesli, chocolate, a second oat milk — on the
    strength of the word "cereals" alone, taking a ~920 kcal smoothie to ~1695."""
    supported = custom_meals.name_supported_by

    # The real failure, and the corrected message that followed it.
    assert supported("breakfast cereals", "0.9l smoothie of 2 big bananas, iat cereals, "
                                          "a spoon and a half of protein powder") is False
    assert supported("breakfast cereals", "0.9l smoothie of 2 big bananas, oat cereals, "
                                          "half liter of oat milk") is False

    # Actually naming it still works, punctuation and word order included.
    assert supported("breakfast cereals", "I ate breakfast cereals") is True
    assert supported("breakfast cereals", "had my breakfast cereals, and a coffee") is True
    assert supported("breakfast cereals", "cereals for breakfast") is True
    assert supported("breakfast cereals", "half a portion of breakfast cereal") is True


def test_saved_meal_match_survives_punctuation_and_plurals():
    assert custom_meals.name_supported_by("protein smoothie", "a protein smoothie.") is True
    assert custom_meals.name_supported_by("protein smoothie", "two protein smoothies!") is True
    assert custom_meals.name_supported_by("protein smoothie", "a smoothie") is False
