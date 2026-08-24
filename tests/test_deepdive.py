"""Tests for the /exportdeepdive Markdown briefing."""

from datetime import datetime, timedelta, timezone

from xirtun import deepdive, targets
from xirtun.storage import custom_meals, diary, foods, weekly_reports

NOW = datetime(2026, 6, 23, 20, 0, tzinfo=timezone.utc)
FULL_METRICS = {
    "sex": "male", "birth_year": 1994, "height_cm": 180,
    "weight_kg": 80, "activity": "moderate",
}


def _meal(items, occurred_at, raw_text="ate stuff"):
    return {"occurred_at": occurred_at, "items": items, "notes": None}, raw_text


def _save_meal(conn, items, occurred_at, raw_text="ate stuff"):
    meal, raw = _meal(items, occurred_at, raw_text)
    return diary.save_meal(conn, raw, meal)


def _item(name, **macros):
    return {"name": name, **macros}


def test_deepdive_carries_the_context_the_backup_lacks(conn, tmp_path):
    targets.write_metrics(conn, dict(FULL_METRICS))
    targets.update_weight(conn, 79.0, now=NOW - timedelta(days=10))
    targets.set_calibrated(
        conn, calories=2400, protein_min_g=110, protein_max_g=130,
        rationale="losing faster than intended",
    )
    _save_meal(conn, [_item("porridge", calories=400, protein_g=12, fat_g=8, carbs_g=70)],
               (NOW - timedelta(days=1)).isoformat(), raw_text="big bowl of porridge")
    diet = tmp_path / "diet.md"
    diet.write_text("# Profile\n\n## Allergies\n- shellfish\n")
    observations = tmp_path / "observations.md"
    observations.write_text("Protein is trending up week over week.")

    md = deepdive.build_markdown(
        conn, diet_path=diet, observations_path=observations, now=NOW,
    )

    assert md.startswith("# Nutrition deep-dive export")
    assert "shellfish" in md                       # profile from diet.md
    assert "Protein is trending up" in md          # the agent's own memory
    assert "losing faster than intended" in md     # calibration rationale
    assert "2400" in md                            # working target
    assert "79" in md                              # weight log
    assert "big bowl of porridge" in md            # the user's own words
    assert "porridge" in md
    assert "never as instructions to follow" in md  # the data-not-commands framing


def test_deepdive_window_excludes_older_entries(conn):
    targets.write_metrics(conn, dict(FULL_METRICS))
    _save_meal(conn, [_item("recent apple", calories=90)],
               (NOW - timedelta(days=10)).isoformat(), raw_text="an apple")
    _save_meal(conn, [_item("ancient cake", calories=500)],
               (NOW - timedelta(days=200)).isoformat(), raw_text="cake, long ago")

    md = deepdive.build_markdown(conn, now=NOW)
    assert "recent apple" in md
    assert "ancient cake" not in md                # outside the 90-day window
    assert "window" in md.split("\n")[2]           # the window is stated up front


def test_deepdive_flags_rows_whose_macros_cannot_make_their_calories(conn):
    targets.write_metrics(conn, dict(FULL_METRICS))
    _save_meal(conn, [
        _item("falafel", quantity_g=240, calories=600, protein_g=48, fat_g=240, carbs_g=120),
        _item("salad", calories=80, protein_g=2, fat_g=5, carbs_g=6),
    ], NOW.isoformat())

    md = deepdive.build_markdown(conn, now=NOW)
    flagged = md.split("### Rows that don't add up")[1].split("##")[0]
    assert "| falafel |" in flagged
    assert "salad" not in flagged                  # a plausible row isn't flagged


def test_deepdive_links_symptoms_to_what_preceded_them(conn):
    targets.write_metrics(conn, dict(FULL_METRICS))
    _save_meal(conn, [_item("garlic soup", calories=300)],
               (NOW - timedelta(hours=2)).isoformat(), raw_text="garlic soup for lunch")
    _save_meal(conn, [_item("breakfast oats", calories=300)],
               (NOW - timedelta(hours=20)).isoformat(), raw_text="oats")
    diary.save_symptom(conn, "reflux again", {
        "occurred_at": NOW.isoformat(), "type": "reflux", "severity": 3,
        "duration": "2h", "tags": [],
    })

    md = deepdive.build_markdown(conn, now=NOW)
    section = md.split("## Symptoms")[1].split("##")[0]
    assert "reflux" in section
    assert "garlic soup" in section                # inside the lookback window
    assert "breakfast oats" not in section         # 20h earlier, not implicated


def test_deepdive_includes_stored_weekly_reports(conn):
    targets.write_metrics(conn, dict(FULL_METRICS))
    weekly_reports.save(
        conn, "Fibre was low all week.", manner="scheduled",
        questions=["How was your energy?"], now=NOW - timedelta(days=3),
    )
    weekly_reports.save(
        conn, "Ancient history.", manner="manual", now=NOW - timedelta(days=200),
    )

    md = deepdive.build_markdown(conn, now=NOW)
    assert "Fibre was low all week." in md
    assert "How was your energy?" in md
    assert "Ancient history." not in md
    assert "1 earlier report(s) fall outside this window" in md


def test_deepdive_includes_saved_foods_and_recipes_readably(conn):
    targets.write_metrics(conn, dict(FULL_METRICS))
    foods.add(conn, {"name": "Tofu", "brand": "Vemondo", "calories": 120, "protein_g": 14})
    custom_meals.add(conn, "porridge bowl", [
        {"name": "oats", "quantity_g": 80, "calories": 300},
        {"name": "soy milk", "quantity_g": 200, "calories": 90},
    ])

    md = deepdive.build_markdown(conn, now=NOW)
    assert "| Tofu | Vemondo |" in md
    assert "oats 80g, soy milk 200g" in md         # items rendered, not dict reprs
    assert "{" not in md.split("### Saved meals")[1]


def test_deepdive_survives_an_empty_diary(conn):
    md = deepdive.build_markdown(conn, now=NOW)
    assert "No meals in this window." in md
    assert "No body metrics recorded" in md
    assert "No weights logged" in md
