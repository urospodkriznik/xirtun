"""Tests for the intake pipeline — deterministic, no network, no cost.

Meals and symptoms share the same shape; the symptom tests mirror the meal ones.
"""

import json
from datetime import datetime, timedelta, timezone

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


def test_structure_meal_retries_an_arithmetically_impossible_estimate():
    """A 240g falafel logged as 240g of FAT is a portion size in the wrong field. It
    reads as a plausible meal forever after, while skewing every average — so it gets
    one corrective retry before it is stored, not an explanation months later."""
    bad = {"needs_clarification": False, "meals": [_meal([
        {"name": "falafel", "quantity_g": 240, "calories": 600,
         "protein_g": 48, "fat_g": 240, "carbs_g": 120},
    ])]}
    good = {"needs_clarification": False, "meals": [_meal([
        {"name": "falafel", "quantity_g": 240, "calories": 600,
         "protein_g": 30, "fat_g": 30, "carbs_g": 60},
    ])]}
    llm = FakeLLM([LLMResponse(data=bad), LLMResponse(data=good)])

    out = structure_meal(llm, "falafel plate")

    assert out["meals"][0]["items"][0]["fat_g"] == 30
    assert len(llm.calls) == 2
    assert "arithmetically impossible" in llm.calls[1]["messages"][-1]["content"]


def test_structure_meal_keeps_the_estimate_when_the_retry_is_no_better():
    """One retry, then move on — a user logging a meal shouldn't be blocked because the
    model can't get its arithmetic straight."""
    bad = {"needs_clarification": False, "meals": [_meal([
        {"name": "falafel", "calories": 600, "protein_g": 48, "fat_g": 240, "carbs_g": 120},
    ])]}
    llm = FakeLLM([LLMResponse(data=bad), LLMResponse(data=bad)])

    out = structure_meal(llm, "falafel plate")

    assert out["meals"][0]["items"][0]["fat_g"] == 240   # stored anyway
    assert len(llm.calls) == 2                           # but only one retry


def test_structure_meal_does_not_retry_a_plausible_estimate():
    data = {"needs_clarification": False, "meals": [_meal([
        {"name": "porridge", "calories": 400, "protein_g": 12, "fat_g": 8, "carbs_g": 70},
    ])]}
    llm = FakeLLM([LLMResponse(data=data)])

    structure_meal(llm, "porridge")

    assert len(llm.calls) == 1


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


def test_symptom_command_logs_directly(conn):
    llm = FakeLLM([
        LLMResponse(data={"needs_clarification": False, "symptoms": [_symptom("fatigue")]}),
    ])
    messenger = FakeMessenger()
    handle_message("/addsymptom low energy", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    assert conn.execute("SELECT COUNT(*) AS n FROM symptoms").fetchone()["n"] == 1
    assert "Symptom logged: fatigue" in messenger.sent[-1]


def test_symptom_command_bare_prompts_then_logs(conn):
    messenger = FakeMessenger()
    handle_message("/addsymptom", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "how are you feeling" in messenger.sent[-1].lower()   # prompt, not a log
    assert conn.execute("SELECT COUNT(*) AS n FROM symptoms").fetchone()["n"] == 0

    llm = FakeLLM([LLMResponse(data={"needs_clarification": False, "symptoms": [_symptom("fatigue")]})])
    handle_message("low energy", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    assert conn.execute("SELECT COUNT(*) AS n FROM symptoms").fetchone()["n"] == 1


def test_symptom_command_can_still_clarify(conn):
    llm = FakeLLM([
        LLMResponse(data={"needs_clarification": True, "question": "How bad, 1-5?"}),
        LLMResponse(data={"needs_clarification": False, "symptoms": [_symptom("headache", severity=4)]}),
    ])
    messenger = FakeMessenger()
    handle_message("/addsymptom off", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    assert messenger.sent[-1] == "How bad, 1-5?"
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


def test_handle_message_exportbackup_dumps_everything_needed_to_restore(conn, tmp_path):
    from xirtun import targets
    from xirtun.storage import foods, weekly_reports

    diary.save_meal(conn, "banana", _meal([{"name": "banana", "calories": 90, "tags": ["fruit"]}]))
    diary.save_symptom(conn, "headache", _symptom("headache", severity=2))
    foods.add(conn, {"name": "Tofu", "calories": 120})
    targets.write_metrics(conn, {
        "sex": "male", "birth_year": 1994, "height_cm": 180,
        "weight_kg": 80, "activity": "moderate",
    })
    targets.update_weight(conn, 78.5)
    targets.set_calibrated(
        conn, calories=2400, protein_min_g=110, protein_max_g=130, rationale="steady",
    )
    weekly_reports.save(conn, "Fibre was low.", manner="scheduled")
    diet = tmp_path / "diet.md"
    diet.write_text("# Profile\n- vegan")
    observations = tmp_path / "observations.md"
    observations.write_text("Protein trending up.")
    messenger = FakeMessenger()

    handle_message(
        "/exportbackup", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger,
        diet_path=diet, observations_path=observations,
    )

    assert len(messenger.documents) == 1
    filename, content, _caption = messenger.documents[0]
    assert filename.endswith(".json")
    data = json.loads(content)

    assert data["version"] == 2
    # The diary itself.
    assert data["meals"][0]["items"][0]["name"] == "banana"
    assert data["meals"][0]["items"][0]["tags"] == ["fruit"]   # JSON column decoded
    assert data["symptoms"][0]["type"] == "headache"
    assert data["known_foods"][0]["name"] == "Tofu"
    # Everything the old version dropped on the floor.
    assert data["metrics"]["height_cm"] == 180
    assert data["targets"]["calibrated"]["calories"] == 2400
    assert data["targets"]["formula"]["calories"] > 0
    assert data["weight_log"][-1]["weight_kg"] == 78.5
    assert data["weekly_reports"][0]["report"] == "Fibre was low."
    assert "vegan" in data["memory"]["diet_md"]
    assert "Protein trending up." in data["memory"]["observations_md"]


def test_exportbackup_includes_diet_history_snapshots(conn, tmp_path):
    from xirtun.memory import diet as memory

    diet = tmp_path / "diet.md"
    memory.write_diet(diet, "# Profile\n- first version")
    memory.write_diet(diet, "# Profile\n- second version")   # snapshots the first
    messenger = FakeMessenger()

    handle_message(
        "/exportbackup", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger,
        diet_path=diet,
    )

    data = json.loads(messenger.documents[0][1])
    assert "second version" in data["memory"]["diet_md"]
    assert len(data["memory"]["diet_history"]) == 1
    assert "first version" in data["memory"]["diet_history"][0]["content"]


def test_handle_message_exportdeepdive_sends_markdown_and_a_privacy_warning(conn, tmp_path):
    from xirtun import targets

    targets.write_metrics(conn, {
        "sex": "male", "birth_year": 1994, "height_cm": 180,
        "weight_kg": 80, "activity": "moderate",
    })
    diary.save_meal(conn, "a banana", _meal([{"name": "banana", "calories": 90}]))
    diet = tmp_path / "diet.md"
    diet.write_text("# Profile\n- vegan")
    messenger = FakeMessenger()

    handle_message(
        "/exportdeepdive", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger,
        diet_path=diet,
    )

    assert len(messenger.documents) == 1
    filename, content, caption = messenger.documents[0]
    assert filename.endswith(".md")
    assert content.startswith("# Nutrition deep-dive export")
    assert "banana" in content and "vegan" in content
    assert "90 days" in caption
    # The warning is a separate message so it isn't lost under a file preview.
    assert "sensitive" in messenger.sent[-1]


def test_handle_message_rejects_the_old_export_command(conn):
    messenger = FakeMessenger()
    handle_message("/export", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert not messenger.documents
    assert "don't recognize" in messenger.sent[-1]


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
    from datetime import datetime, timedelta, timezone

    from xirtun import targets
    targets.write_metrics(conn, {"sex": "female", "birth_year": 1994, "height_cm": 165, "weight_kg": 60, "activity": "light"})
    now = datetime(2026, 6, 29, 8, 0, tzinfo=timezone.utc)
    targets.update_weight(conn, 60.5, now=now - timedelta(days=14))
    targets.update_weight(conn, 60.0, now=now)
    messenger = FakeMessenger()
    handle_message("/target", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger, now=now)
    assert "kcal" in messenger.sent[-1]
    assert "trend" in messenger.sent[-1].lower()  # weight trend appended


def test_weight_command_updates_metric(conn):
    from xirtun import targets
    messenger = FakeMessenger()
    handle_message("/addweight 72", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "72" in messenger.sent[-1]
    assert targets.read_metrics(conn)["weight_kg"] == 72


def test_food_command_registers(conn):
    from xirtun.storage import foods
    llm = FakeLLM([LLMResponse(data={
        "name": "Lidl vegan sausage", "calories": 250, "protein_g": 18, "fat_g": 12, "carbs_g": 4, "tags": [],
    })])
    messenger = FakeMessenger()
    handle_message("/savefood Lidl vegan sausage: 250kcal 18p 12f 4c", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
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


def test_late_meal_triggers_upright_nudge_once_per_evening(conn):
    now = datetime(2026, 7, 6, 21, 30, tzinfo=timezone.utc)

    def llm_for(occurred):
        return FakeLLM([
            LLMResponse(data={"intent": "meal"}),
            LLMResponse(data={"needs_clarification": False, "meals": [
                _meal([{"name": "toast", "calories": 200}], occurred_at=occurred),
            ]}),
        ])

    messenger = FakeMessenger()
    handle_message("toast at 21:00", chat_id="c1", llm=llm_for("2026-07-06T21:00:00"),
                   conn=conn, messenger=messenger, now=now)
    assert any("stay upright" in m for m in messenger.sent)

    # A second late entry the same evening must not nag again.
    messenger2 = FakeMessenger()
    handle_message("cookies", chat_id="c1", llm=llm_for("2026-07-06T21:20:00"),
                   conn=conn, messenger=messenger2, now=now)
    assert not any("stay upright" in m for m in messenger2.sent)


def test_backdated_late_meal_gets_no_nudge(conn):
    """Logging yesterday's 22:00 dinner this morning — 'stay upright now' would be
    nonsense, so the nudge only fires when eating time is within ~90 min of logging."""
    now = datetime(2026, 7, 7, 9, 0, tzinfo=timezone.utc)
    llm = FakeLLM([
        LLMResponse(data={"intent": "meal"}),
        LLMResponse(data={"needs_clarification": False, "meals": [
            _meal([{"name": "pasta", "calories": 600}], occurred_at="2026-07-06T22:00:00"),
        ]}),
    ])
    messenger = FakeMessenger()
    handle_message("last night I ate pasta at 10pm", chat_id="c1", llm=llm,
                   conn=conn, messenger=messenger, now=now)
    assert not any("stay upright" in m for m in messenger.sent)


def test_late_meal_recap_logged_hours_later_gets_no_nudge(conn):
    """A same-evening recap typed well after eating (2h) shouldn't fire 'stay upright now'."""
    now = datetime(2026, 7, 6, 23, 15, tzinfo=timezone.utc)   # logged 2h15m after eating
    llm = FakeLLM([
        LLMResponse(data={"intent": "meal"}),
        LLMResponse(data={"needs_clarification": False, "meals": [
            _meal([{"name": "pasta", "calories": 600}], occurred_at="2026-07-06T21:00:00"),
        ]}),
    ])
    messenger = FakeMessenger()
    handle_message("at 9pm I ate pasta", chat_id="c1", llm=llm,
                   conn=conn, messenger=messenger, now=now)
    assert not any("stay upright" in m for m in messenger.sent)


def test_late_beverage_only_meal_gets_no_nudge(conn):
    """A late beer isn't a full stomach — no stay-upright nudge."""
    now = datetime(2026, 7, 6, 20, 32, tzinfo=timezone.utc)
    llm = FakeLLM([
        LLMResponse(data={"intent": "meal"}),
        LLMResponse(data={"needs_clarification": False, "meals": [
            _meal([{"name": "beer", "calories": 215}], occurred_at="2026-07-06T20:31:00"),
        ]}),
    ])
    messenger = FakeMessenger()
    handle_message("i had 0.5l beer", chat_id="c1", llm=llm, conn=conn, messenger=messenger, now=now)
    assert not any("stay upright" in m for m in messenger.sent)


def test_meal_sodium_stored_and_acked(conn):
    llm = FakeLLM([
        LLMResponse(data={"intent": "meal"}),
        LLMResponse(data={"needs_clarification": False, "meals": [
            _meal([{"name": "vegan sausage", "calories": 265, "sodium_mg": 900},
                   {"name": "white bread", "calories": 208, "sodium_mg": 460}]),
        ]}),
    ])
    messenger = FakeMessenger()
    noon = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)   # daytime: no late-meal nudge
    handle_message("sausage and bread", chat_id="c1", llm=llm, conn=conn, messenger=messenger, now=noon)

    row = conn.execute("SELECT SUM(sodium_mg) AS s FROM meal_items").fetchone()
    assert row["s"] == 1360
    assert "1360mg sodium" in messenger.sent[-1]


def test_known_food_sodium_overrides(conn):
    """A saved label's sodium wins over the model's guess, scaled by quantity."""
    from xirtun.storage import foods
    foods.add(conn, {"name": "vemondo sausage", "calories": 250, "sodium_mg": 800, "tags": []})
    llm = FakeLLM([
        LLMResponse(data={"intent": "meal"}),
        LLMResponse(data={"needs_clarification": False, "meals": [
            _meal([{"name": "vemondo sausage", "known_food": "vemondo sausage",
                    "quantity_g": 50, "sodium_mg": 5}]),
        ]}),
    ])
    noon = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    handle_message("50g vemondo sausage", chat_id="c1", llm=llm, conn=conn,
                   messenger=FakeMessenger(), now=noon)

    row = conn.execute("SELECT sodium_mg FROM meal_items").fetchone()
    assert row["sodium_mg"] == 400.0        # 800 per 100g x 50g, not the model's 5


def test_meal_fiber_stored_and_acked(conn):
    llm = FakeLLM([
        LLMResponse(data={"intent": "meal"}),
        LLMResponse(data={"needs_clarification": False, "meals": [
            _meal([{"name": "lentils", "calories": 230, "fiber_g": 15.6},
                   {"name": "seeded bread", "calories": 208, "fiber_g": 6.0}]),
        ]}),
    ])
    messenger = FakeMessenger()
    noon = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)   # daytime: no late-meal nudge
    handle_message("lentils and seeded bread", chat_id="c1", llm=llm, conn=conn, messenger=messenger, now=noon)

    row = conn.execute("SELECT SUM(fiber_g) AS f FROM meal_items").fetchone()
    assert row["f"] == 21.6
    assert "22g fibre" in messenger.sent[-1]           # rounded total in the ack


def test_known_food_fiber_overrides(conn):
    from xirtun.storage import foods
    foods.add(conn, {"name": "seeded bread", "calories": 250, "fiber_g": 7.0, "tags": []})
    llm = FakeLLM([
        LLMResponse(data={"intent": "meal"}),
        LLMResponse(data={"needs_clarification": False, "meals": [
            _meal([{"name": "seeded bread", "known_food": "seeded bread", "quantity_g": 100, "calories": 999}]),
        ]}),
    ])
    handle_message("100g seeded bread", chat_id="c1", llm=llm, conn=conn, messenger=FakeMessenger())

    row = conn.execute("SELECT fiber_g FROM meal_items").fetchone()
    assert row["fiber_g"] == 7.0                       # label value, per 100g × 100g


def test_known_food_match_renames_to_saved_name(conn):
    """The confirmation shows the real saved product's name, not whatever generic
    name the model used — so a wrong match is visible instead of hidden."""
    from xirtun.storage import foods
    foods.add(conn, {"name": "combino chickpeas pasta", "calories": 228, "protein_g": 14, "fat_g": 3, "carbs_g": 38, "tags": []})
    llm = FakeLLM([
        LLMResponse(data={"intent": "meal"}),
        LLMResponse(data={"needs_clarification": False, "meals": [
            {"occurred_at": None, "notes": None, "items": [
                {"name": "spinach pasta", "known_food": "combino chickpeas pasta", "quantity_g": 200, "calories": 300},
            ]},
        ]}),
    ])
    messenger = FakeMessenger()
    noon = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)   # daytime: no late-meal nudge
    handle_message("plate of spinach pasta", chat_id="c1", llm=llm, conn=conn, messenger=messenger, now=noon)

    assert "combino chickpeas pasta" in messenger.sent[-1]
    row = conn.execute("SELECT name FROM meal_items").fetchone()
    assert row["name"] == "combino chickpeas pasta"


def test_myfood_lists_saved(conn):
    from xirtun.storage import foods
    foods.add(conn, {"name": "Tofu", "calories": 120, "protein_g": 12})
    messenger = FakeMessenger()
    handle_message("/foodlist", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
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

    handle_message("/savefood myway falafel: 200 kcal, 20g protein", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    assert "update" in messenger.sent[-1].lower()  # offered update/add/cancel

    handle_message("update", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "myway falafel" not in foods.names(conn)                              # not added as new
    assert foods.find_by_name(conn, "myway vegan falafels")["calories"] == 200    # existing updated


def test_food_duplicate_cancel_saves_nothing(conn):
    from xirtun.storage import foods
    foods.add(conn, {"name": "myway vegan falafels", "calories": 214})
    llm = FakeLLM([LLMResponse(data={"name": "myway falafel", "calories": 200})])
    messenger = FakeMessenger()

    handle_message("/savefood myway falafel: 200 kcal", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    handle_message("cancel", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)

    assert "myway falafel" not in foods.names(conn)                              # not added
    assert foods.find_by_name(conn, "myway vegan falafels")["calories"] == 214    # unchanged


def test_meal_item_stores_sugar(conn):
    diary.save_meal(conn, "soda", _meal([{"name": "cola", "carbs_g": 39, "sugar_g": 39}]))
    row = conn.execute("SELECT carbs_g, sugar_g FROM meal_items").fetchone()
    assert row["carbs_g"] == 39
    assert row["sugar_g"] == 39


def test_known_food_stores_and_shows_sugar(conn):
    from xirtun.storage import foods
    foods.add(conn, {"name": "Cola", "calories": 42, "carbs_g": 10.6, "sugar_g": 10.6})
    assert foods.find_by_name(conn, "Cola")["sugar_g"] == 10.6
    messenger = FakeMessenger()
    handle_message("/foodlist", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "S11" in messenger.sent[-1]   # sugar rendered in the food line


def test_custom_meal_totals_sugar(conn):
    from xirtun.storage import custom_meals
    custom_meals.add(conn, "snack", [
        {"name": "cookie", "carbs_g": 20, "sugar_g": 12},
        {"name": "juice", "carbs_g": 25, "sugar_g": 22},
    ])
    row = conn.execute("SELECT sugar_g FROM custom_meals WHERE name = 'snack'").fetchone()
    assert row["sugar_g"] == 34


def test_delmeal_removes(conn):
    from xirtun.storage import custom_meals
    custom_meals.add(conn, "morning oats", [{"name": "oats", "calories": 300}])
    messenger = FakeMessenger()
    handle_message("/delmeal morning oats", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "morning oats" not in custom_meals.names(conn)
    assert "Removed" in messenger.sent[-1]


def test_delmeal_suggests_closest_then_confirms(conn):
    from xirtun.storage import custom_meals
    custom_meals.add(conn, "morning oats", [{"name": "oats", "calories": 300}])
    messenger = FakeMessenger()

    handle_message("/delmeal morning", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "Did you mean 'morning oats'?" in messenger.sent[-1]
    assert "morning oats" in custom_meals.names(conn)

    handle_message("yes", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "morning oats" not in custom_meals.names(conn)
    assert "Removed" in messenger.sent[-1]


def test_delmeal_suggestion_cancelled(conn):
    from xirtun.storage import custom_meals
    custom_meals.add(conn, "morning oats", [{"name": "oats", "calories": 300}])
    messenger = FakeMessenger()

    handle_message("/delmeal morning", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    handle_message("nope", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)

    assert "morning oats" in custom_meals.names(conn)
    assert "Cancelled" in messenger.sent[-1]


def test_delmeal_no_match_at_all(conn):
    messenger = FakeMessenger()
    handle_message("/delmeal pizza", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "No saved meal named 'pizza'." in messenger.sent[-1]


def test_delfood_removes(conn):
    from xirtun.storage import foods
    foods.add(conn, {"name": "Tofu", "calories": 120})
    messenger = FakeMessenger()
    handle_message("/delfood Tofu", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "Tofu" not in foods.names(conn)
    assert "Removed" in messenger.sent[-1]


def test_delfood_suggests_closest_then_confirms(conn):
    from xirtun.storage import foods
    foods.add(conn, {"name": "Trader Joe's cookies", "calories": 480})
    messenger = FakeMessenger()

    handle_message("/delfood trader joe cookies", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "Did you mean 'Trader Joe's cookies'?" in messenger.sent[-1]
    assert "Trader Joe's cookies" in foods.names(conn)            # not yet removed

    handle_message("yes", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "Trader Joe's cookies" not in foods.names(conn)
    assert "Removed" in messenger.sent[-1]


def test_delfood_suggestion_cancelled(conn):
    from xirtun.storage import foods
    foods.add(conn, {"name": "Trader Joe's cookies", "calories": 480})
    messenger = FakeMessenger()

    handle_message("/delfood trader joe cookies", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    handle_message("nope", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)

    assert "Trader Joe's cookies" in foods.names(conn)            # kept
    assert "Cancelled" in messenger.sent[-1]


def test_delfood_no_match_at_all(conn):
    messenger = FakeMessenger()
    handle_message("/delfood pizza", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "No saved food named 'pizza'." in messenger.sent[-1]


def test_undo_includes_saved_food(conn):
    from xirtun.storage import foods
    foods.add(conn, {"name": "Tofu", "calories": 120})
    messenger = FakeMessenger()

    handle_message("/undo", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "saved food: Tofu" in messenger.sent[-1]

    handle_message("yes", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "Tofu" not in foods.names(conn)


def test_undo_includes_saved_meal(conn):
    from xirtun.storage import custom_meals
    custom_meals.add(conn, "lunch", [{"name": "rice", "calories": 200}])
    messenger = FakeMessenger()

    handle_message("/undo", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "saved meal: lunch" in messenger.sent[-1]

    handle_message("yes", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "lunch" not in custom_meals.names(conn)


def test_undo_includes_note_when_most_recent(conn, tmp_path):
    from datetime import timedelta

    from xirtun.memory import diet as memory

    now = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
    diet_path = tmp_path / "diet.md"
    diet_path.write_text("# Profile\n")
    diary.save_meal(conn, "banana", _meal([{"name": "banana"}]), now=now)
    memory.append_note(diet_path, "feel low energy", now=now + timedelta(minutes=5))
    messenger = FakeMessenger()

    handle_message(
        "/undo", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger, diet_path=diet_path,
    )
    assert "note: feel low energy" in messenger.sent[-1]

    handle_message(
        "yes", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger, diet_path=diet_path,
    )
    assert "feel low energy" not in memory.read_diet(diet_path)
    assert conn.execute("SELECT COUNT(*) AS n FROM meals").fetchone()["n"] == 1  # meal untouched


def test_undo_prefers_newer_diary_entry_over_older_note(conn, tmp_path):
    from datetime import timedelta

    from xirtun.memory import diet as memory

    now = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
    diet_path = tmp_path / "diet.md"
    diet_path.write_text("# Profile\n")
    memory.append_note(diet_path, "feel low energy", now=now)
    diary.save_symptom(conn, "fatigue", _symptom("fatigue"), now=now + timedelta(minutes=5))
    messenger = FakeMessenger()

    handle_message(
        "/undo", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger, diet_path=diet_path,
    )
    assert "symptom: fatigue" in messenger.sent[-1]

    handle_message(
        "yes", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger, diet_path=diet_path,
    )
    assert conn.execute("SELECT COUNT(*) AS n FROM symptoms").fetchone()["n"] == 0
    assert "feel low energy" in memory.read_diet(diet_path)  # note untouched


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

    handle_message("/addworkout", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    assert "workout" in messenger.sent[-1].lower()   # prompt

    handle_message("ran 5k", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    assert conn.execute("SELECT COUNT(*) AS n FROM exercises").fetchone()["n"] == 1


def test_meal_command_bare_prompts_then_logs(conn):
    messenger = FakeMessenger()
    handle_message("/addmeal", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "what did you eat" in messenger.sent[-1].lower()   # prompt, no LLM call yet

    noon = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    llm = FakeLLM([LLMResponse(data={"needs_clarification": False,
                                     "meals": [_meal([{"name": "rice", "calories": 200}])]})])
    handle_message("rice", chat_id="c1", llm=llm, conn=conn, messenger=messenger, now=noon)
    assert conn.execute("SELECT COUNT(*) AS n FROM meals").fetchone()["n"] == 1


def test_savefood_bare_prompts_then_saves(conn):
    from xirtun.storage import foods
    messenger = FakeMessenger()
    handle_message("/savefood", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "label" in messenger.sent[-1].lower()   # prompt

    llm = FakeLLM([LLMResponse(data={"name": "Tofu", "calories": 120})])
    handle_message("Tofu: 120 kcal", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    assert "Tofu" in foods.names(conn)


def test_delfood_bare_prompts_then_deletes(conn):
    from xirtun.storage import foods
    foods.add(conn, {"name": "Tofu", "calories": 120})
    messenger = FakeMessenger()
    handle_message("/delfood", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "which saved food" in messenger.sent[-1].lower()   # prompt

    handle_message("Tofu", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "Tofu" not in foods.names(conn)


def test_pending_command_cancel_logs_nothing(conn):
    messenger = FakeMessenger()
    handle_message("/addmeal", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    handle_message("cancel", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "Cancelled" in messenger.sent[-1]
    assert conn.execute("SELECT COUNT(*) AS n FROM meals").fetchone()["n"] == 0


def test_pending_command_slash_cancel(conn):
    messenger = FakeMessenger()
    handle_message("/addmeal", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    handle_message("/cancel", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "Cancelled" in messenger.sent[-1]
    assert conn.execute("SELECT COUNT(*) AS n FROM meals").fetchone()["n"] == 0


def test_pending_command_dropped_when_other_command_runs(conn):
    """Running a different command mid-prompt drops the pending one, so a later plain
    message isn't captured as the abandoned command's input."""
    diary.save_meal(conn, "x", _meal([{"name": "banana", "calories": 100}]))
    messenger = FakeMessenger()

    handle_message("/addmeal", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    handle_message("/today", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "banana" in messenger.sent[-1]   # today report, not still awaiting a meal

    # A following plain message must be classified fresh, not swallowed as a meal.
    handle_message("hello there", chat_id="c1", llm=FakeLLM([LLMResponse(data={"intent": "other"})]),
                   conn=conn, messenger=messenger)
    assert conn.execute("SELECT COUNT(*) AS n FROM meals").fetchone()["n"] == 1  # only the seeded one


def test_pending_command_switches_on_new_slash_command(conn, tmp_path):
    """Mid-prompt, a slash command means 'I changed my mind' — drop the pending meal
    and run the new command instead."""
    diet = tmp_path / "diet.md"
    diet.write_text("# Profile\n")
    messenger = FakeMessenger()

    handle_message("/addmeal", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger, diet_path=diet)
    handle_message("/addnote I feel full", chat_id="c1", llm=FakeLLM(), conn=conn,
                   messenger=messenger, diet_path=diet)

    assert "I feel full" in diet.read_text()
    assert conn.execute("SELECT COUNT(*) AS n FROM meals").fetchone()["n"] == 0


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


def test_log_partial_portion_of_saved_meal_scales_macros(conn):
    from xirtun.storage import custom_meals

    custom_meals.add(conn, "breakfast cereals", [
        {"name": "muesli", "quantity_g": 75, "calories": 280, "protein_g": 8},
        {"name": "oat milk", "quantity_g": 250, "calories": 120, "protein_g": 3},
    ])
    log_llm = FakeLLM([
        LLMResponse(data={"intent": "meal"}),
        LLMResponse(data={"needs_clarification": False, "meals": [
            {"occurred_at": None, "notes": None, "items": [
                {"name": "breakfast cereals", "custom_meal": "breakfast cereals", "portion": 0.5},
            ]},
        ]}),
    ])
    messenger = FakeMessenger()
    handle_message("I ate half a portion of breakfast cereals", chat_id="c1",
                   llm=log_llm, conn=conn, messenger=messenger)

    rows = conn.execute("SELECT calories, quantity_g FROM meal_items ORDER BY id").fetchall()
    assert [r["calories"] for r in rows] == [140, 60]      # 280*0.5, 120*0.5
    assert [r["quantity_g"] for r in rows] == [37.5, 125]  # 75*0.5, 250*0.5


def test_log_full_portion_of_saved_meal_unscaled(conn):
    from xirtun.storage import custom_meals

    custom_meals.add(conn, "lunch bowl", [{"name": "rice", "calories": 300, "quantity_g": 200}])
    log_llm = FakeLLM([
        LLMResponse(data={"intent": "meal"}),
        LLMResponse(data={"needs_clarification": False, "meals": [
            {"occurred_at": None, "notes": None,
             "items": [{"name": "lunch bowl", "custom_meal": "lunch bowl"}]},   # no portion -> full
        ]}),
    ])
    messenger = FakeMessenger()
    handle_message("I ate lunch bowl", chat_id="c1", llm=log_llm, conn=conn, messenger=messenger)
    row = conn.execute("SELECT calories, quantity_g FROM meal_items").fetchone()
    assert row["calories"] == 300 and row["quantity_g"] == 200


def test_unrecognized_slash_command_is_rejected(conn):
    """A slash message that matches no command must not be guess-classified (e.g. an old
    /note becoming a symptom); it gets an 'unknown command' reply instead."""
    llm = FakeLLM([LLMResponse(data={"intent": "symptom"})])  # would misfire if reached
    messenger = FakeMessenger()
    handle_message("/note I feel bloated", chat_id="c1", llm=llm, conn=conn, messenger=messenger)
    assert "don't recognize that command" in messenger.sent[-1]
    assert conn.execute("SELECT COUNT(*) AS n FROM symptoms").fetchone()["n"] == 0


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


def test_handle_message_addwaist_logs_and_shows_the_trend(conn):
    from xirtun import targets

    messenger = FakeMessenger()
    now = datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc)
    targets.add_waist(conn, 86.0, now=now - timedelta(days=30))

    handle_message("/addwaist 84", chat_id="c1", llm=FakeLLM(), conn=conn,
                   messenger=messenger, now=now)

    assert "84 cm" in messenger.sent[-1]
    assert "86cm → 84cm" in messenger.sent[-1]          # trend comes back immediately
    assert len(targets.waist_history(conn)) == 2


def test_handle_message_addwaist_prompts_when_bare(conn):
    messenger = FakeMessenger()
    handle_message("/addwaist", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    assert "waist in cm" in messenger.sent[-1]

    handle_message("84.5", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger)
    from xirtun import targets
    assert targets.waist_history(conn)[-1]["waist_cm"] == 84.5


def test_format_ack_shows_the_assumed_quantity():
    """The ack once showed only kcal and protein, so a dropped portion count ('three
    portions' logged as one) looked identical to a correct estimate. Showing grams makes
    it catchable at the moment it happens."""
    ack = format_ack([_meal([
        {"name": "pasta", "quantity_g": 450, "calories": 600, "protein_g": 20},
        {"name": "beer", "calories": 105, "protein_g": 1},          # no quantity known
    ])])
    assert "- pasta 450g (~600 kcal" in ack
    assert "- beer (~105 kcal" in ack                                # omitted, not "0g"


def test_structure_prompt_requires_scaling_by_stated_portions():
    """Measured against the real API: without this rule the cheap model logged "3
    portions of pasta" as 1.35x "a portion" (150g vs 225g); with it, exactly 3x."""
    from xirtun.pipeline.structure import STRUCTURE_SYSTEM

    assert "TOTAL amount eaten" in STRUCTURE_SYSTEM
    assert "Never collapse a stated multiple" in STRUCTURE_SYSTEM
    assert "the beer is still one small glass" in STRUCTURE_SYSTEM   # scale only what's counted
