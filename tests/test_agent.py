"""Tests for the weekly agent loop, driven by a scripted FakeLLM.

`test_weekly_runs_tools_then_finishes` proves the loop: it calls tools in order,
feeds results back, and stops when the model finishes — all deterministic, no
network. run_weekly() only computes the result (report + questions); it never
sends anything itself — that's run_weekly.py's job (see test_runs.py).
"""

import json
from datetime import timezone

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

    assert "2026-07-06: 2 meal(s), ~1200 kcal, 70g protein, 12g fibre" in out  # per-day
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
