"""Tests for the storage layer: schema creation and the key/value helpers."""

from xirtun.storage import db


def test_tables_exist(conn):
    names = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    # <= means "is a subset of": every name on the left must be present.
    assert {"meals", "meal_items", "symptoms", "pending", "runs", "kv"} <= names


def test_kv_round_trip(conn):
    assert db.kv_get(conn, "offset") is None       # missing key -> None
    db.kv_set(conn, "offset", "42")
    assert db.kv_get(conn, "offset") == "42"
    db.kv_set(conn, "offset", "43")                # upsert overwrites
    assert db.kv_get(conn, "offset") == "43"
