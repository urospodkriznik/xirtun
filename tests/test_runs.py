"""Tests for run tracking and the weekly-review idempotency guard, plus the
manual-vs-scheduled delivery split (hold-and-ask vs report-then-follow-up)."""

from datetime import datetime, timedelta, timezone

from xirtun.llm.base import LLMResponse
from xirtun.llm.fake import FakeLLM
from xirtun.messaging.fake import FakeMessenger
from xirtun.pipeline import weekly_qa
from xirtun.run_weekly import run_weekly_review
from xirtun.storage import runs

TZ = timezone.utc
CHAT_ID = "c1"


def _finishing_llm(*, final_message="hi", questions=None):
    return FakeLLM([
        LLMResponse(data={
            "thought": "t", "tool": None, "args_json": "{}",
            "final_message": final_message, "questions": questions or [],
        }),
    ])


def _review(conn, tmp_path, llm, *, now, force, messenger=None, manner="scheduled"):
    return run_weekly_review(
        llm=llm,
        conn=conn,
        diet_path=tmp_path / "diet.md",
        observations_path=tmp_path / "observations.md",
        messenger=messenger or FakeMessenger(),
        tz=TZ,
        chat_id=CHAT_ID,
        manner=manner,
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
        observations_path=tmp_path / "o.md", messenger=messenger, tz=TZ,
        chat_id=CHAT_ID, now=now, force=False,
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


def test_scheduled_run_sends_report_then_followup_questions(conn, tmp_path):
    """Scheduled: no one's guaranteed to be around, so the report goes out right
    away; questions follow as a separate message and open a 'capture' session."""
    now = datetime(2026, 1, 8, 9, 0, tzinfo=TZ)
    messenger = FakeMessenger()
    llm = _finishing_llm(final_message="Your week in review.", questions=["Feeling hungry often?"])

    _review(conn, tmp_path, llm, now=now, force=True, messenger=messenger, manner="scheduled")

    assert messenger.sent[0] == "Your week in review."
    assert "Feeling hungry often?" in messenger.sent[1]
    qa = weekly_qa.get(conn, CHAT_ID, now=now)
    assert qa is not None and qa.mode == "capture"


def test_manual_run_holds_report_until_questions_answered(conn, tmp_path):
    """Manual /weekly: withhold the report and ask first — the user is at the
    keyboard, so front-loading the calibrating questions is worth it."""
    now = datetime(2026, 1, 8, 9, 0, tzinfo=TZ)
    messenger = FakeMessenger()
    llm = _finishing_llm(final_message="Your week in review.", questions=["Feeling hungry often?"])

    _review(conn, tmp_path, llm, now=now, force=True, messenger=messenger, manner="manual")

    assert "Your week in review." not in messenger.sent   # held back
    assert any("Feeling hungry often?" in m for m in messenger.sent)
    qa = weekly_qa.get(conn, CHAT_ID, now=now)
    assert qa is not None and qa.mode == "interactive" and qa.report == "Your week in review."


def test_manual_run_with_no_questions_sends_immediately(conn, tmp_path):
    now = datetime(2026, 1, 8, 9, 0, tzinfo=TZ)
    messenger = FakeMessenger()
    llm = _finishing_llm(final_message="Nothing to flag this week.")

    _review(conn, tmp_path, llm, now=now, force=True, messenger=messenger, manner="manual")

    assert messenger.sent == ["Nothing to flag this week."]
    assert weekly_qa.get(conn, CHAT_ID, now=now) is None
