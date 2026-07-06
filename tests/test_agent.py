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


def test_weekly_respects_max_iters(conn, tmp_path):
    # Model never finishes (always asks for a tool) -> loop must stop and send nothing.
    llm = FakeLLM([LLMResponse(data=_action(tool="read_diet")) for _ in range(10)])

    result = run_weekly(
        llm=llm, conn=conn, diet_path=tmp_path / "diet.md",
        observations_path=tmp_path / "observations.md", tz=timezone.utc, max_iters=3,
    )

    assert result.report == ""
    assert result.questions == []
