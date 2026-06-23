"""Tests for destructive data reset."""

from xirtun.storage import admin, diary


def test_reset_all_clears_db_and_files(conn, tmp_path):
    diary.save_meal(conn, "x", {"occurred_at": None, "items": [{"name": "a"}], "notes": None})
    (tmp_path / "diet.md").write_text("# Profile")
    (tmp_path / "observations.md").write_text("notes")
    (tmp_path / "diet.history").mkdir()
    (tmp_path / "diet.history" / "diet-old.md").write_text("old")

    admin.reset_all(conn, tmp_path)

    assert conn.execute("SELECT COUNT(*) AS n FROM meals").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM meal_items").fetchone()["n"] == 0
    assert not (tmp_path / "diet.md").exists()
    assert not (tmp_path / "observations.md").exists()
    assert not (tmp_path / "diet.history").exists()
