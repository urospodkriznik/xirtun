"""Tests for the weekly agent loop, driven by a scripted FakeLLM.

`test_weekly_runs_tools_then_finishes` proves the loop: it calls tools in order,
feeds results back, and stops when the model finishes — all deterministic, no
network. run_weekly() only computes the result (report + questions); it never
sends anything itself — that's run_weekly.py's job (see test_runs.py).
"""

import json
from datetime import datetime, timedelta, timezone

from xirtun.agent.tools import ToolContext, build_dispatch
from xirtun.agent.weekly import run_weekly
from xirtun.llm.base import LLMResponse
from xirtun.llm.fake import FakeLLM


def _action(*, tool=None, args=None, final_message=None, questions=None, thought="t"):
    return {
        "thought": thought,
        "tool": tool,
        "args_json": json.dumps(args or {}),
        "final_message": final_message,
        "questions": questions or [],
    }


def test_weekly_runs_tools_then_finishes(conn, tmp_path):
    diet = tmp_path / "diet.md"
    diet.write_text("# Profile")
    obs = tmp_path / "observations.md"

    llm = FakeLLM([
        LLMResponse(data=_action(tool="read_observations")),
        LLMResponse(data=_action(tool="query_diary", args={"since_days": 7})),
        LLMResponse(data=_action(tool="write_observations", args={"content": "veg intake up"})),
        LLMResponse(data=_action(final_message="Nice week — your veggie intake is up.")),
    ])

    result = run_weekly(
        llm=llm, conn=conn, diet_path=diet, observations_path=obs, tz=timezone.utc,
    )

    assert result.report == "Nice week — your veggie intake is up."
    assert result.questions == []
    assert obs.read_text() == "veg intake up"        # the write_observations tool ran


def test_weekly_surfaces_calibrating_questions(conn, tmp_path):
    llm = FakeLLM([
        LLMResponse(data=_action(
            final_message="Report body.",
            questions=["Do you feel hungry often?", ""],   # blanks get filtered
        )),
    ])

    result = run_weekly(
        llm=llm, conn=conn, diet_path=tmp_path / "diet.md",
        observations_path=tmp_path / "observations.md", tz=timezone.utc,
    )

    assert result.report == "Report body."
    assert result.questions == ["Do you feel hungry often?"]


def test_weekly_finishing_with_empty_message_sends_nothing(conn, tmp_path):
    llm = FakeLLM([LLMResponse(data=_action(final_message=""))])

    result = run_weekly(
        llm=llm, conn=conn, diet_path=tmp_path / "diet.md",
        observations_path=tmp_path / "observations.md", tz=timezone.utc,
    )

    assert result.report == ""
    assert result.questions == []
    assert result.incomplete is False   # genuinely decided to say nothing, not cut off


def test_agent_can_calibrate_targets(conn, tmp_path):
    """The set_targets tool persists a calibrated working target the user then sees."""
    from xirtun import targets
    from xirtun.agent.tools import ToolContext, build_dispatch
    from datetime import datetime

    targets.write_metrics(conn, {"sex": "male", "birth_year": 1994, "height_cm": 180,
                                 "weight_kg": 80, "activity": "moderate"})
    ctx = ToolContext(conn=conn, diet_path=tmp_path / "d.md",
                      observations_path=tmp_path / "o.md", now=datetime(2026, 7, 6, 17, 0))
    dispatch = build_dispatch(ctx)

    result = dispatch["set_targets"]({
        "calories": 2300, "protein_min_g": 110, "protein_max_g": 130,
        "rationale": "injury week; user reports fullness at higher intake",
    })
    assert "2300" in result
    assert "2300" in dispatch["get_targets"]({})       # visible on next read
    assert targets.read_calibrated(conn)["calories"] == 2300


def test_intake_summary_computes_daily_totals_and_target_comparison(conn, tmp_path):
    """The agent's numbers come from SQL, not model arithmetic: per-day totals,
    averages over logged days only, working-target comparison, late-meal list."""
    from datetime import datetime
    from xirtun import targets
    from xirtun.agent.tools import ToolContext, build_dispatch
    from xirtun.storage import diary

    targets.write_metrics(conn, {"sex": "male", "birth_year": 1994, "height_cm": 180,
                                 "weight_kg": 80, "activity": "moderate"})
    targets.set_calibrated(conn, calories=2400, protein_min_g=110, protein_max_g=130,
                           rationale="test calibration")

    def meal(occurred_at, kcal, protein, fiber):
        return {"occurred_at": occurred_at, "notes": None,
                "items": [{"name": "x", "calories": kcal, "protein_g": protein, "fiber_g": fiber}]}

    # This week (now = 07-08): two logged days.
    diary.save_meal(conn, "a", meal("2026-07-06T09:00:00", 500, 30, 8))
    diary.save_meal(conn, "b", meal("2026-07-06T21:15:00", 700, 40, 4))   # late meal
    diary.save_meal(conn, "c", meal("2026-07-07T12:00:00", 1200, 50, 10))
    # Last week: higher intake, for the week-over-week delta.
    diary.save_meal(conn, "d", meal("2026-06-30T12:00:00", 2400, 100, 20))

    ctx = ToolContext(conn=conn, diet_path=tmp_path / "d.md",
                      observations_path=tmp_path / "o.md", now=datetime(2026, 7, 8, 17, 0))
    out = build_dispatch(ctx)["get_intake_summary"]({"weeks": 4})

    assert "2026-07-06: 2 meal(s), ~1200 kcal, 70g protein" in out            # per-day
    assert "12g fibre" in out
    assert "This week: ~1200 kcal" in out              # avg per logged day, this week
    assert "1 wk ago: ~2400 kcal" in out               # week-over-week row
    assert "-1200 kcal (-50%)" in out                  # this-week-vs-last delta, in code
    assert "calibrated): ~2400 kcal/day" in out
    assert "50% of target" in out                      # 1200/2400, computed in code
    assert "2026-07-06 21:15" in out                   # late-meal listed


def test_weekly_respects_max_iters(conn, tmp_path):
    # Model never finishes (always asks for a tool) -> loop must stop and send nothing.
    llm = FakeLLM([LLMResponse(data=_action(tool="read_diet")) for _ in range(10)])

    result = run_weekly(
        llm=llm, conn=conn, diet_path=tmp_path / "diet.md",
        observations_path=tmp_path / "observations.md", tz=timezone.utc, max_iters=3,
    )

    assert result.report == ""
    assert result.questions == []
    assert result.incomplete is True


def test_intake_summary_carries_every_macro_and_a_long_window(conn, tmp_path):
    """Regression: the summary once reported only calories/protein/fibre over 4 weeks,
    so a steadily climbing sugar intake was invisible to an agent explicitly asked to
    report on sugar — it had no honest way to see it."""
    from xirtun.storage import diary

    def meal(occurred_at, kcal, sugar):
        return {"occurred_at": occurred_at, "notes": None, "items": [
            {"name": "x", "calories": kcal, "protein_g": 40, "fat_g": 30,
             "carbs_g": 200, "sugar_g": sugar, "fiber_g": 20}]}

    now = datetime(2026, 7, 8, 17, 0)
    # Sugar climbing across 8 weeks — invisible in any single week.
    for weeks_ago, sugar in enumerate((90, 80, 70, 60, 50, 40, 30, 20)):
        day = now - timedelta(days=weeks_ago * 7 + 1)
        diary.save_meal(conn, "m", meal(day.isoformat(), 2000, sugar))

    ctx = ToolContext(conn=conn, diet_path=tmp_path / "d.md",
                      observations_path=tmp_path / "o.md", now=now)
    out = build_dispatch(ctx)["get_intake_summary"]({})

    assert "90g sugar" in out and "20g sugar" in out    # both ends of the trend visible
    assert "30g fat" in out and "200g carbs" in out
    assert "7 wks ago" in out                           # 12-week default, not 4


def test_intake_summary_reports_when_tracking_started(conn, tmp_path):
    """Sugar and fibre were added to the schema after logging began. Averaging across
    that boundary understates them, so the agent is told where the data starts."""
    from xirtun.storage import diary

    now = datetime(2026, 7, 8, 17, 0)
    diary.save_meal(conn, "old", {"occurred_at": (now - timedelta(days=40)).isoformat(),
                                  "notes": None, "items": [{"name": "x", "calories": 500}]})
    diary.save_meal(conn, "new", {"occurred_at": (now - timedelta(days=2)).isoformat(),
                                  "notes": None, "items": [
                                      {"name": "y", "calories": 500, "sugar_g": 20, "fiber_g": 10}]})

    ctx = ToolContext(conn=conn, diet_path=tmp_path / "d.md",
                      observations_path=tmp_path / "o.md", now=now)
    out = build_dispatch(ctx)["get_intake_summary"]({})
    assert "Tracking began mid-window" in out
    assert "UNDERSTATES" in out


def test_food_frequency_counts_days_with_a_denominator(conn, tmp_path):
    from xirtun.storage import diary

    now = datetime(2026, 7, 8, 17, 0)
    for days_ago in (1, 3, 5):
        diary.save_meal(conn, "f", {"occurred_at": (now - timedelta(days=days_ago)).isoformat(),
                                    "notes": None, "items": [{"name": "Flaxseed", "calories": 60}]})
    diary.save_meal(conn, "c", {"occurred_at": (now - timedelta(days=2)).isoformat(),
                                "notes": None, "items": [{"name": "cake", "calories": 400}]})

    ctx = ToolContext(conn=conn, diet_path=tmp_path / "d.md",
                      observations_path=tmp_path / "o.md", now=now)
    out = build_dispatch(ctx)["get_food_frequency"]({"days": 30})

    assert "flaxseed: 3x on 3 of 4 logged day(s)" in out    # name normalised, denominator given
    assert "cake: 1x on 1 of 4" in out


def test_compare_symptom_days_puts_the_sample_size_up_front(conn, tmp_path):
    from xirtun.storage import diary

    now = datetime(2026, 7, 8, 17, 0)

    def day(days_ago, kcal, hour=13):
        when = (now - timedelta(days=days_ago)).replace(hour=hour)
        diary.save_meal(conn, "m", {"occurred_at": when.isoformat(), "notes": None,
                                    "items": [{"name": "x", "calories": kcal, "fiber_g": 30}]})
        return when

    bloat_day = day(2, 900, hour=21)      # big late meal
    day(4, 500)
    day(6, 500)
    diary.save_symptom(conn, "bloated", {"occurred_at": bloat_day.isoformat(),
                                         "type": "bloating", "severity": 2, "tags": []})

    ctx = ToolContext(conn=conn, diet_path=tmp_path / "d.md",
                      observations_path=tmp_path / "o.md", now=now)
    out = build_dispatch(ctx)["compare_symptom_days"]({"symptom": "bloating", "days": 30})

    assert "with bloating (1 day(s))" in out
    assert "without (2 day(s))" in out
    assert "~900 kcal after 20:00" in out
    assert "n = 1 symptom day(s)" in out                    # sample size, not just a claim
    assert "hypothesis, not a finding" in out


def test_compare_symptom_days_without_the_symptom(conn, tmp_path):
    ctx = ToolContext(conn=conn, diet_path=tmp_path / "d.md",
                      observations_path=tmp_path / "o.md", now=datetime(2026, 7, 8, 17, 0))
    out = build_dispatch(ctx)["compare_symptom_days"]({"symptom": "reflux"})
    assert "No days with 'reflux'" in out


def test_intake_summary_says_when_a_nutrient_was_never_recorded(conn, tmp_path):
    """A nutrient with no values anywhere sums to 0 every week. Reported bare, that
    reads as 'ate no fibre for 12 weeks' — a shortfall the user never had."""
    from xirtun.storage import diary

    now = datetime(2026, 7, 8, 17, 0)
    diary.save_meal(conn, "m", {"occurred_at": (now - timedelta(days=1)).isoformat(),
                                "notes": None,
                                "items": [{"name": "x", "calories": 500, "protein_g": 20}]})

    ctx = ToolContext(conn=conn, diet_path=tmp_path / "d.md",
                      observations_path=tmp_path / "o.md", now=now)
    out = build_dispatch(ctx)["get_intake_summary"]({})

    assert "NEVER RECORDED in any entry" in out
    assert "fiber" in out and "sugar" in out
    assert "absence of data, not measured zeros" in out
