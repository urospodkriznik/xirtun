"""Tests for the intake pipeline — deterministic, no network, no cost.

Meals and symptoms share the same shape; the symptom tests mirror the meal ones.
"""

import json
from datetime import datetime, timezone

from xirtun.llm.base import LLMResponse
from xirtun.llm.fake import FakeLLM
from xirtun.messaging.fake import FakeMessenger
from xirtun.pipeline import sessions
from xirtun.pipeline.classify import classify
from xirtun.pipeline.intake import format_ack, format_symptom_ack, handle_message
from xirtun.pipeline.structure import structure_meal
from xirtun.storage import diary


def _meal(items, occurred_at=None):
    return {"occurred_at": occurred_at, "items": items, "notes": None}


def _symptom(type_, occurred_at=None, severity=None, duration=None, tags=None):
    return {
        "occurred_at": occurred_at,
        "type": type_,
        "severity": severity,
        "duration": duration,
        "tags": tags or [],
    }


# --- meals ---

def test_classify_returns_intent():
    llm = FakeLLM([LLMResponse(data={"intent": "meal"})])
    assert classify(llm, "I ate a banana") == "meal"


def test_structure_meal_returns_meals():
    data = {"needs_clarification": False, "meals": [_meal([{"name": "banana", "calories": 90}])]}
    llm = FakeLLM([LLMResponse(data=data)])
    out = structure_meal(llm, "a banana")
    assert out["meals"][0]["items"][0]["name"] == "banana"


def test_format_ack_single_and_multiple():
    assert "150" in format_ack([_meal([{"name": "a", "calories": 90}, {"name": "b", "calories": 60}])])
    assert "2 meals" in format_ack([_meal([{"name": "a"}]), _meal([{"name": "b"}])])


def test_save_meal_inserts_rows(conn):
    meal = _meal([
        {"name": "banana", "quantity_g": 120, "calories": 105, "tags": ["fruit"]},
    ])
    meal_id = diary.save_meal(conn, "a banana", meal)
    rows = conn.execute("SELECT name, tags FROM meal_items WHERE meal_id = ?", (meal_id,)).fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0]["tags"]) == ["fruit"]


def test_save_meal_uses_inferred_occurred_at(conn):
    meal_id = diary.save_meal(conn, "lunch yesterday", _meal([{"name": "soup"}], occurred_at="2026-06-20T12:30:00"))
    row = conn.execute("SELECT occurred_at FROM meals WHERE id = ?", (meal_id,)).fetchone()
    assert row["occurred_at"].startswith("2026-06-20T12:30")


def test_handle_message_meal_happy_path(conn):
    llm = FakeLLM([
        LLMResponse(data={"intent": "meal"}),
        LLMResponse(data={"needs_clarification": False, "meals": [_meal([{"name": "banana", "calories": 90}])]}),
    ])
    messenger = FakeMessenger()
    handle_message("I ate a banana", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    assert conn.execute("SELECT COUNT(*) AS n FROM meals").fetchone()["n"] == 1
    assert messenger.sent and "banana" in messenger.sent[0]


def test_clarification_then_complete(conn):
    llm = FakeLLM([
        LLMResponse(data={"intent": "meal"}),
        LLMResponse(data={"needs_clarification": True, "question": "How much?"}),
        LLMResponse(data={"needs_clarification": False, "meals": [_meal([{"name": "curry", "calories": 600}])]}),
    ])
    messenger = FakeMessenger()
    handle_message("I had curry", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    assert messenger.sent[-1] == "How much?"
    assert sessions.get_active(conn, "c1") is not None

    handle_message("about 2 cups", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    assert conn.execute("SELECT COUNT(*) AS n FROM meals").fetchone()["n"] == 1
    assert sessions.get_active(conn, "c1") is None


def test_multiple_meals_stored_separately(conn):
    llm = FakeLLM([
        LLMResponse(data={"intent": "meal"}),
        LLMResponse(data={"needs_clarification": False, "meals": [
            _meal([{"name": "salad", "calories": 200}], occurred_at="2026-06-22T12:30:00"),
            _meal([{"name": "pasta", "calories": 600}], occurred_at="2026-06-22T19:00:00"),
        ]}),
    ])
    messenger = FakeMessenger()
    handle_message("lunch salad, dinner pasta", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    assert conn.execute("SELECT COUNT(*) AS n FROM meals").fetchone()["n"] == 2


# --- symptoms ---

def test_format_symptom_ack():
    ack = format_symptom_ack([_symptom("bloating", severity=3), _symptom("headache")])
    assert "bloating (severity 3/5)" in ack and "headache" in ack


def test_save_symptom_inserts_row(conn):
    sid = diary.save_symptom(conn, "bloated this morning",
                             _symptom("bloating", occurred_at="2026-06-22T08:00:00", severity=3, tags=["gut"]))
    row = conn.execute("SELECT type, severity, occurred_at, tags FROM symptoms WHERE id = ?", (sid,)).fetchone()
    assert row["type"] == "bloating"
    assert row["severity"] == 3
    assert row["occurred_at"].startswith("2026-06-22T08:00")
    assert json.loads(row["tags"]) == ["gut"]


def test_handle_message_symptom_happy_path(conn):
    llm = FakeLLM([
        LLMResponse(data={"intent": "symptom"}),
        LLMResponse(data={"needs_clarification": False, "symptoms": [_symptom("bloating", severity=3)]}),
    ])
    messenger = FakeMessenger()
    handle_message("I'm really bloated", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    assert conn.execute("SELECT COUNT(*) AS n FROM symptoms").fetchone()["n"] == 1
    assert messenger.sent and "bloating" in messenger.sent[-1]


def test_symptom_clarification_routes_back_to_symptom(conn):
    llm = FakeLLM([
        LLMResponse(data={"intent": "symptom"}),
        LLMResponse(data={"needs_clarification": True, "question": "How bad, 1-5?"}),
        LLMResponse(data={"needs_clarification": False, "symptoms": [_symptom("headache", severity=4)]}),
    ])
    messenger = FakeMessenger()
    handle_message("I feel off", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    assert messenger.sent[-1] == "How bad, 1-5?"
    # the follow-up must route back to the SYMPTOM processor (session.kind == "symptom")
    handle_message("a 4", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    assert conn.execute("SELECT COUNT(*) AS n FROM symptoms").fetchone()["n"] == 1


def test_handle_message_note_appended_to_diet(conn, tmp_path):
    diet = tmp_path / "diet.md"
    diet.write_text("# Profile\n")
    llm = FakeLLM([LLMResponse(data={"intent": "note"})])
    messenger = FakeMessenger()

    handle_message("I want to gain muscle", chat_id="c1", llm=llm, conn=conn,
                   messenger=messenger, diet_path=diet)

    assert "gain muscle" in diet.read_text()
    assert messenger.sent


def test_delete_last_removes_most_recent(conn):
    t1 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc)
    diary.save_meal(conn, "banana", _meal([{"name": "banana"}]), now=t1)
    diary.save_symptom(conn, "bloated", _symptom("bloating"), now=t2)

    assert diary.delete_last(conn).startswith("symptom")   # symptom was newer
    assert conn.execute("SELECT COUNT(*) AS n FROM symptoms").fetchone()["n"] == 0
    assert diary.delete_last(conn).startswith("meal")      # meal now newest
    assert conn.execute("SELECT COUNT(*) AS n FROM meals").fetchone()["n"] == 0
    assert diary.delete_last(conn) is None                 # nothing left


def test_handle_message_undo(conn):
    diary.save_meal(conn, "banana", _meal([{"name": "banana"}]))
    messenger = FakeMessenger()

    handle_message("/undo", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)

    assert conn.execute("SELECT COUNT(*) AS n FROM meals").fetchone()["n"] == 0
    assert "Removed" in messenger.sent[-1]


def test_handle_message_help(conn):
    messenger = FakeMessenger()
    handle_message("/help", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "/undo" in messenger.sent[-1]


def test_handle_message_profile(conn, tmp_path):
    diet = tmp_path / "diet.md"
    diet.write_text("# Profile\n- vegan")
    messenger = FakeMessenger()
    handle_message("/profile", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger, diet_path=diet)
    assert "vegan" in messenger.sent[-1]


def test_handle_message_today(conn):
    now = datetime(2026, 6, 23, 20, 0, tzinfo=timezone.utc)
    diary.save_meal(
        conn, "lunch",
        _meal([{"name": "banana", "calories": 100}], occurred_at=now.replace(hour=12).isoformat()),
    )
    messenger = FakeMessenger()
    handle_message("/today", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger, now=now)
    assert "banana" in messenger.sent[-1]


def test_handle_message_shopping_intent(conn, tmp_path):
    diet = tmp_path / "diet.md"
    diet.write_text("# Profile\n- vegan")
    llm = FakeLLM([
        LLMResponse(data={"intent": "shopping"}),               # classify
        LLMResponse(text="Buy: spinach, lentils, walnuts."),    # suggestion (no schema)
    ])
    messenger = FakeMessenger()

    handle_message("what should I buy?", chat_id="c1", llm=llm, conn=conn, messenger=messenger, diet_path=diet)

    assert "spinach" in messenger.sent[-1]


def test_shop_command(conn, tmp_path):
    diet = tmp_path / "diet.md"
    diet.write_text("# Profile")
    llm = FakeLLM([LLMResponse(text="Buy: oats, eggs.")])  # command -> no classify call
    messenger = FakeMessenger()

    handle_message("/shop", chat_id="c1", llm=llm, conn=conn, messenger=messenger, diet_path=diet)

    assert "oats" in messenger.sent[-1]


def test_target_command(conn):
    from xirtun import targets
    targets.write_metrics(conn, {"sex": "female", "birth_year": 1994, "height_cm": 165, "weight_kg": 60, "activity": "light"})
    messenger = FakeMessenger()
    handle_message("/target", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "kcal" in messenger.sent[-1]


def test_weight_command_updates_metric(conn):
    from xirtun import targets
    messenger = FakeMessenger()
    handle_message("/weight 72", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "72" in messenger.sent[-1]
    assert targets.read_metrics(conn)["weight_kg"] == 72


def test_handle_message_other(conn):
    llm = FakeLLM([LLMResponse(data={"intent": "other"})])
    messenger = FakeMessenger()
    handle_message("how are you?", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    assert messenger.sent
    assert conn.execute("SELECT COUNT(*) AS n FROM meals").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM symptoms").fetchone()["n"] == 0
