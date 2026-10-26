"""Tests for destructive data reset."""

from xirtun.messaging.telegram import OFFSET_KEY
from xirtun.storage import admin, db, diary


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


def test_reset_all_empties_every_table(conn, tmp_path):
    """Every table, including ones added after this test was written: put a row in each
    (whatever its columns), wipe, and nothing may be left."""
    for table in admin.user_tables(conn):
        cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        # Fill every column with something its type accepts; skip the rowid alias.
        values = {c["name"]: (1 if "INT" in c["type"].upper() or "REAL" in c["type"].upper() else "x")
                  for c in cols if not c["pk"] or table == "kv"}
        if table == "meal_items":
            values["meal_id"] = conn.execute("SELECT id FROM meals").fetchone()["id"]
        names = ", ".join(f'"{n}"' for n in values)
        marks = ", ".join("?" for _ in values)
        conn.execute(f'INSERT INTO "{table}" ({names}) VALUES ({marks})', list(values.values()))
    conn.commit()

    admin.reset_all(conn, tmp_path)

    for table in admin.user_tables(conn):
        assert conn.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()["n"] == 0, table


def test_reset_all_wipes_kv_but_keeps_the_polling_offset(conn, tmp_path):
    db.kv_set(conn, "metrics", '{"weight_kg": 70}')
    db.kv_set(conn, "nutrition_watchlist", '{"items": []}')
    db.set_timezone(conn, "Europe/Berlin")
    db.kv_set(conn, OFFSET_KEY, "123")

    admin.reset_all(conn, tmp_path)

    rows = {r["k"]: r["v"] for r in conn.execute("SELECT k, v FROM kv").fetchall()}
    assert rows == {OFFSET_KEY: "123"}


def test_kept_key_is_the_telegram_offset():
    # admin can't import messaging (storage sits below it), so this pins the two together.
    assert admin.KEEP_KV_KEYS == (OFFSET_KEY,)
