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


def test_handle_message_undo_confirms_then_removes(conn):
    diary.save_meal(conn, "banana", _meal([{"name": "banana"}]))
    messenger = FakeMessenger()

    handle_message("/undo", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "remove" in messenger.sent[-1].lower()
    assert conn.execute("SELECT COUNT(*) AS n FROM meals").fetchone()["n"] == 1  # not yet

    handle_message("yes", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert conn.execute("SELECT COUNT(*) AS n FROM meals").fetchone()["n"] == 0
    assert "Removed" in messenger.sent[-1]


def test_handle_message_undo_cancel_keeps_entry(conn):
    diary.save_meal(conn, "banana", _meal([{"name": "banana"}]))
    messenger = FakeMessenger()

    handle_message("/undo", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    handle_message("nope", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)

    assert conn.execute("SELECT COUNT(*) AS n FROM meals").fetchone()["n"] == 1  # kept
    assert "Cancelled" in messenger.sent[-1]


def test_handle_message_help(conn):
    messenger = FakeMessenger()
    handle_message("/help", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "/undo" in messenger.sent[-1]


def test_handle_message_export_dumps_diary(conn):
    from xirtun.storage import foods

    diary.save_meal(conn, "banana", _meal([{"name": "banana", "calories": 90, "tags": ["fruit"]}]))
    diary.save_symptom(conn, "headache", _symptom("headache", severity=2))
    foods.add(conn, {"name": "Tofu", "calories": 120})
    messenger = FakeMessenger()

    handle_message("/export", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)

    assert len(messenger.documents) == 1
    filename, content, _caption = messenger.documents[0]
    assert filename.endswith(".json")
    data = json.loads(content)
    assert data["meals"][0]["items"][0]["name"] == "banana"
    assert data["meals"][0]["items"][0]["tags"] == ["fruit"]   # JSON column decoded
    assert data["symptoms"][0]["type"] == "headache"
    assert data["known_foods"][0]["name"] == "Tofu"


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


def test_food_command_registers(conn):
    from xirtun.storage import foods
    llm = FakeLLM([LLMResponse(data={
        "name": "Lidl vegan sausage", "calories": 250, "protein_g": 18, "fat_g": 12, "carbs_g": 4, "tags": [],
    })])
    messenger = FakeMessenger()
    handle_message("/food Lidl vegan sausage: 250kcal 18p 12f 4c", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    assert "Saved" in messenger.sent[-1]
    assert "Lidl vegan sausage" in foods.names(conn)


def test_food_intent_registers(conn):
    from xirtun.storage import foods
    llm = FakeLLM([
        LLMResponse(data={"intent": "food"}),
        LLMResponse(data={"name": "Tofu", "calories": 120, "protein_g": 12, "fat_g": 7, "carbs_g": 2, "tags": []}),
    ])
    messenger = FakeMessenger()
    handle_message("save tofu: 120 kcal per 100g, 12g protein", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    assert "Tofu" in foods.names(conn)


def test_known_food_overrides_macros(conn):
    from xirtun.storage import foods
    foods.add(conn, {"name": "vegan sausage", "brand": "Lidl", "calories": 250, "protein_g": 18, "fat_g": 12, "carbs_g": 4, "tags": []})
    llm = FakeLLM([
        LLMResponse(data={"intent": "meal"}),
        LLMResponse(data={"needs_clarification": False, "meals": [
            {"occurred_at": None, "notes": None, "items": [
                {"name": "vegan sausage", "known_food": "vegan sausage", "quantity_g": 200, "calories": 999},
            ]},
        ]}),
    ])
    messenger = FakeMessenger()
    handle_message("200g vegan sausage", chat_id="c1", llm=llm, conn=conn, messenger=messenger)

    row = conn.execute("SELECT calories, protein_g FROM meal_items").fetchone()
    assert row["calories"] == 500   # 250/100 * 200, overriding the model's 999
    assert row["protein_g"] == 36   # 18/100 * 200


def test_myfood_lists_saved(conn):
    from xirtun.storage import foods
    foods.add(conn, {"name": "Tofu", "calories": 120, "protein_g": 12})
    messenger = FakeMessenger()
    handle_message("/myfood", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "Tofu" in messenger.sent[-1]


def test_checkfood_finds_similar(conn):
    from xirtun.storage import foods
    foods.add(conn, {"name": "myway vegan falafels", "calories": 214})
    messenger = FakeMessenger()
    handle_message("/checkfood myway falafel", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "falafel" in messenger.sent[-1].lower()


def test_food_duplicate_confirm_then_update(conn):
    from xirtun.storage import foods
    foods.add(conn, {"name": "myway vegan falafels", "calories": 214, "protein_g": 23})
    llm = FakeLLM([LLMResponse(data={"name": "myway falafel", "calories": 200, "protein_g": 20})])
    messenger = FakeMessenger()

    handle_message("/food myway falafel: 200 kcal, 20g protein", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    assert "update" in messenger.sent[-1].lower()  # offered update/add/cancel

    handle_message("update", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "myway falafel" not in foods.names(conn)                              # not added as new
    assert foods.find_by_name(conn, "myway vegan falafels")["calories"] == 200    # existing updated


def test_food_duplicate_cancel_saves_nothing(conn):
    from xirtun.storage import foods
    foods.add(conn, {"name": "myway vegan falafels", "calories": 214})
    llm = FakeLLM([LLMResponse(data={"name": "myway falafel", "calories": 200})])
    messenger = FakeMessenger()

    handle_message("/food myway falafel: 200 kcal", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    handle_message("cancel", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)

    assert "myway falafel" not in foods.names(conn)                              # not added
    assert foods.find_by_name(conn, "myway vegan falafels")["calories"] == 214    # unchanged


def test_delfood_removes(conn):
    from xirtun.storage import foods
    foods.add(conn, {"name": "Tofu", "calories": 120})
    messenger = FakeMessenger()
    handle_message("/delfood Tofu", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "Tofu" not in foods.names(conn)
    assert "Removed" in messenger.sent[-1]


def _exercise(type_, **kw):
    base = {"occurred_at": None, "type": type_, "duration_min": None, "intensity": None,
            "calories_burned": None, "distance_km": None, "notes": None, "tags": []}
    base.update(kw)
    return base


def test_save_exercise_inserts(conn):
    eid = diary.save_exercise(conn, "ran 5k", _exercise("running", duration_min=30, calories_burned=300))
    row = conn.execute("SELECT type, duration_min, calories_burned FROM exercises WHERE id = ?", (eid,)).fetchone()
    assert row["type"] == "running"
    assert row["calories_burned"] == 300


def test_handle_message_exercise(conn):
    llm = FakeLLM([
        LLMResponse(data={"intent": "exercise"}),
        LLMResponse(data={"needs_clarification": False,
                          "exercises": [_exercise("running", duration_min=30, calories_burned=300)]}),
    ])
    messenger = FakeMessenger()
    handle_message("I ran 5k this morning", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    assert conn.execute("SELECT COUNT(*) AS n FROM exercises").fetchone()["n"] == 1
    assert "running" in messenger.sent[-1].lower()


def test_exercise_command_opens_session_then_logs(conn):
    llm = FakeLLM([
        LLMResponse(data={"needs_clarification": False,
                          "exercises": [_exercise("running", calories_burned=200)]}),
    ])
    messenger = FakeMessenger()

    handle_message("/exercise", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    assert "what did you do" in messenger.sent[-1].lower()

    handle_message("ran 5k", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    assert conn.execute("SELECT COUNT(*) AS n FROM exercises").fetchone()["n"] == 1


def test_undo_includes_exercise(conn):
    diary.save_exercise(conn, "ran", _exercise("running"))
    messenger = FakeMessenger()
    handle_message("/undo", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "exercise" in messenger.sent[-1].lower()


def test_savemeal_and_log_by_name_expands(conn):
    from xirtun.storage import custom_meals

    save_llm = FakeLLM([LLMResponse(data={"needs_clarification": False, "meals": [
        {"occurred_at": None, "notes": None, "items": [
            {"name": "muesli", "quantity_g": 75, "calories": 280, "protein_g": 8},
            {"name": "oat milk", "quantity_g": 250, "calories": 120, "protein_g": 3},
        ]},
    ]})])
    messenger = FakeMessenger()
    handle_message("/savemeal breakfast cereals: 75g muesli, 250ml oat milk", chat_id="c1", llm=save_llm, conn=conn, messenger=messenger)
    assert "breakfast cereals" in custom_meals.names(conn)
    assert "Saved meal" in messenger.sent[-1]

    log_llm = FakeLLM([
        LLMResponse(data={"intent": "meal"}),
        LLMResponse(data={"needs_clarification": False, "meals": [
            {"occurred_at": None, "notes": None,
             "items": [{"name": "breakfast cereals", "custom_meal": "breakfast cereals"}]},
        ]}),
    ])
    handle_message("I ate breakfast cereals", chat_id="c1", llm=log_llm, conn=conn, messenger=messenger)

    assert conn.execute("SELECT COUNT(*) AS n FROM meals").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM meal_items").fetchone()["n"] == 2  # expanded


def test_lastmeals_command(conn):
    diary.save_meal(conn, "x", _meal([{"name": "banana", "calories": 100}]))
    messenger = FakeMessenger()
    handle_message("/lastmeals", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "banana" in messenger.sent[-1]


def test_handle_message_other(conn):
    llm = FakeLLM([LLMResponse(data={"intent": "other"})])
    messenger = FakeMessenger()
    handle_message("how are you?", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    assert messenger.sent
    assert conn.execute("SELECT COUNT(*) AS n FROM meals").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM symptoms").fetchone()["n"] == 0
