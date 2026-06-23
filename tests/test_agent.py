"""Tests for the weekly agent loop, driven by a scripted FakeLLM.

`test_weekly_runs_tools_then_finishes` proves the loop: it calls tools in order,
feeds results back, and stops when the model finishes — all deterministic, no
network.
"""

import json
from datetime import timezone

from xirtun.agent.weekly import run_weekly
from xirtun.llm.base import LLMResponse
from xirtun.llm.fake import FakeLLM
from xirtun.messaging.fake import FakeMessenger


def _action(*, tool=None, args=None, final_message=None, thought="t"):
    return {
        "thought": thought,
        "tool": tool,
        "args_json": json.dumps(args or {}),
        "final_message": final_message,
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
    messenger = FakeMessenger()

    result = run_weekly(
        llm=llm, conn=conn, diet_path=diet, observations_path=obs,
        messenger=messenger, tz=timezone.utc,
    )

    assert result == "Nice week — your veggie intake is up."
    assert messenger.sent == ["Nice week — your veggie intake is up."]
    assert obs.read_text() == "veg intake up"        # the write_observations tool ran


def test_weekly_finishing_with_empty_message_sends_nothing(conn, tmp_path):
    llm = FakeLLM([LLMResponse(data=_action(final_message=""))])
    messenger = FakeMessenger()

    result = run_weekly(
        llm=llm, conn=conn, diet_path=tmp_path / "diet.md",
        observations_path=tmp_path / "observations.md", messenger=messenger, tz=timezone.utc,
    )

    assert result == ""
    assert messenger.sent == []


def test_weekly_respects_max_iters(conn, tmp_path):
    # Model never finishes (always asks for a tool) -> loop must stop and send nothing.
    llm = FakeLLM([LLMResponse(data=_action(tool="read_diet")) for _ in range(10)])
    messenger = FakeMessenger()

    result = run_weekly(
        llm=llm, conn=conn, diet_path=tmp_path / "diet.md",
        observations_path=tmp_path / "observations.md", messenger=messenger,
        tz=timezone.utc, max_iters=3,
    )

    assert result is None
    assert messenger.sent == []
