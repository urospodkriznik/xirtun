"""Tests for run tracking and the weekly-review idempotency guard."""

from datetime import datetime, timedelta, timezone

from xirtun.llm.base import LLMResponse
from xirtun.llm.fake import FakeLLM
from xirtun.messaging.fake import FakeMessenger
from xirtun.run_weekly import run_weekly_review
from xirtun.storage import runs

TZ = timezone.utc


def _finishing_llm():
    """A FakeLLM that finishes the agent loop immediately with a message."""
    return FakeLLM([
        LLMResponse(data={"thought": "t", "tool": None, "args_json": "{}", "final_message": "hi"}),
    ])


def _review(conn, tmp_path, llm, *, now, force):
    return run_weekly_review(
        llm=llm,
        conn=conn,
        diet_path=tmp_path / "diet.md",
        observations_path=tmp_path / "observations.md",
        messenger=FakeMessenger(),
        tz=TZ,
        now=now,
        force=force,
    )


def test_runs_record_and_last_ok(conn):
    now = datetime(2026, 1, 1, 9, 0, tzinfo=TZ)
    run_id = runs.start(conn, now)
    assert runs.last_ok_at(conn) is None          # still "running", not counted
    runs.finish(conn, run_id, now, "ok")
    assert runs.last_ok_at(conn) == now


def test_review_skipped_when_recent(conn, tmp_path):
    now = datetime(2026, 1, 8, 9, 0, tzinfo=TZ)
    rid = runs.start(conn, now - timedelta(days=1))
    runs.finish(conn, rid, now - timedelta(days=1), "ok")

    messenger = FakeMessenger()
    ran = run_weekly_review(
        llm=_finishing_llm(), conn=conn, diet_path=tmp_path / "d.md",
        observations_path=tmp_path / "o.md", messenger=messenger, tz=TZ, now=now, force=False,
    )
    assert ran is False
    assert messenger.sent == []


def test_review_runs_when_due(conn, tmp_path):
    now = datetime(2026, 1, 8, 9, 0, tzinfo=TZ)
    rid = runs.start(conn, now - timedelta(days=8))
    runs.finish(conn, rid, now - timedelta(days=8), "ok")

    assert _review(conn, tmp_path, _finishing_llm(), now=now, force=False) is True


def test_force_runs_even_if_recent(conn, tmp_path):
    now = datetime(2026, 1, 8, 9, 0, tzinfo=TZ)
    rid = runs.start(conn, now)
    runs.finish(conn, rid, now, "ok")

    assert _review(conn, tmp_path, _finishing_llm(), now=now, force=True) is True


def test_first_run_has_no_guard(conn, tmp_path):
    now = datetime(2026, 1, 8, 9, 0, tzinfo=TZ)
    assert _review(conn, tmp_path, _finishing_llm(), now=now, force=False) is True
