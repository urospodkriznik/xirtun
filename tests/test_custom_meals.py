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
