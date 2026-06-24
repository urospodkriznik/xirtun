"""Tests for the custom food database (known_foods)."""

from xirtun.storage import foods


def test_add_and_lookup(conn):
    foods.add(conn, {"name": "Oat milk", "calories": 45, "protein_g": 1, "fat_g": 1.5, "carbs_g": 6, "tags": []})
    assert "Oat milk" in foods.names(conn)
    found = foods.find_by_name(conn, "oat milk")  # case-insensitive
    assert found is not None and found["calories"] == 45


def test_for_prompt_includes_package(conn):
    foods.add(conn, {"name": "Falafel", "calories": 214, "protein_g": 23, "package_g": 200})
    entry = next(f for f in foods.for_prompt(conn) if f["name"] == "Falafel")
    assert entry["package_g"] == 200
    assert foods.find_by_name(conn, "Falafel")["protein_g"] == 23


def test_search_finds_similar_excludes_exact(conn):
    foods.add(conn, {"name": "myway vegan falafels", "calories": 214})
    assert "myway vegan falafels" in foods.search(conn, "myway falafel")
    assert foods.search(conn, "myway vegan falafels") == []  # exact name excluded


def test_fiber_stored(conn):
    foods.add(conn, {"name": "Falafel", "calories": 214, "fiber_g": 4.6})
    assert foods.find_by_name(conn, "Falafel")["fiber_g"] == 4.6


def test_all_rows(conn):
    foods.add(conn, {"name": "Tofu", "calories": 120})
    assert any(row["name"] == "Tofu" for row in foods.all_rows(conn))


def test_delete(conn):
    foods.add(conn, {"name": "Tofu", "calories": 120})
    assert foods.delete(conn, "tofu") is True   # case-insensitive
    assert foods.delete(conn, "tofu") is False  # already gone


def test_upsert_updates_existing(conn):
    foods.add(conn, {"name": "Oat milk", "calories": 45})
    foods.add(conn, {"name": "Oat milk", "calories": 50})
    assert foods.names(conn).count("Oat milk") == 1
    assert foods.find_by_name(conn, "Oat milk")["calories"] == 50
