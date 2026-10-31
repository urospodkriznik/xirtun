"""Tests for run tracking and the weekly-review idempotency guard, plus the
manual-vs-scheduled delivery split (hold-and-ask vs report-then-follow-up)."""

from datetime import datetime, timedelta, timezone

from xirtun.llm.base import LLMResponse
from xirtun.llm.fake import FakeLLM
from xirtun.messaging.fake import FakeMessenger
from xirtun.pipeline import weekly_qa
from xirtun.run_weekly import run_weekly_review
from xirtun.storage import runs, weekly_reports

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


def _log_meal(conn, when):
    from xirtun.storage import diary

    diary.save_meal(conn, "toast", {"occurred_at": None, "items": [{"name": "toast"}], "notes": None},
                    now=when)


def test_first_review_waits_a_week_from_the_first_entry(conn, tmp_path):
    """A new person has no review on record; that must not mean 'overdue' — the day
    after onboarding there is one day of food to look at, not a week."""
    start = datetime(2026, 1, 1, 12, 0, tzinfo=TZ)
    _log_meal(conn, start)

    day_two = start + timedelta(days=1, hours=5)
    assert _review(conn, tmp_path, _finishing_llm(), now=day_two, force=False) is False

    day_eight = start + timedelta(days=7, hours=1)
    assert _review(conn, tmp_path, _finishing_llm(), now=day_eight, force=False) is True


def test_first_review_counts_from_logging_time_not_backdated_meals(conn, tmp_path):
    start = datetime(2026, 1, 8, 12, 0, tzinfo=TZ)
    from xirtun.storage import diary

    # "last week I had..." — occurred long ago, but logged just now.
    diary.save_meal(conn, "old", {"occurred_at": "2025-12-20T12:00:00+00:00",
                                  "items": [{"name": "toast"}], "notes": None}, now=start)

    assert _review(conn, tmp_path, _finishing_llm(), now=start + timedelta(hours=5), force=False) is False


def test_nothing_logged_means_nothing_to_review(conn, tmp_path):
    now = datetime(2026, 1, 8, 9, 0, tzinfo=TZ)
    messenger = FakeMessenger()
    assert _review(conn, tmp_path, _finishing_llm(), now=now, force=False, messenger=messenger) is False
    assert messenger.sent == []


def test_force_still_reviews_a_new_person(conn, tmp_path):
    start = datetime(2026, 1, 1, 12, 0, tzinfo=TZ)
    _log_meal(conn, start)
    assert _review(conn, tmp_path, _finishing_llm(), now=start + timedelta(hours=5), force=True) is True


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


def test_incomplete_run_does_not_reset_guard_or_send_partial_report(conn, tmp_path):
    """Regression: if the agent runs out of tool-call turns before writing the
    report (real bug hit in prod on 2026-07-13 — set_targets/write_observations ran,
    but max_iters was exhausted before the finishing turn), the run must NOT count
    as 'ok'. Marking it ok would silently reset the 7-day guard with nothing ever
    sent, so the user gets nothing for another full week."""
    now = datetime(2026, 1, 8, 17, 0, tzinfo=TZ)
    _log_meal(conn, now - timedelta(days=8))   # has been logging for over a week: due
    messenger = FakeMessenger()
    # Never finishes — every turn asks for a tool, so run_weekly exhausts max_iters.
    llm = FakeLLM([
        LLMResponse(data={"thought": "t", "tool": "read_diet", "args_json": "{}",
                          "final_message": None, "questions": []})
        for _ in range(30)
    ])

    ran = _review(conn, tmp_path, llm, now=now, force=True, messenger=messenger, manner="scheduled")

    assert ran is True
    assert not any("Your week in review" in m for m in messenger.sent)  # no partial report
    assert any("didn't finish" in m for m in messenger.sent)            # user is told
    assert runs.last_ok_at(conn) is None                                 # guard NOT reset

    # A normal scheduled retry (force=False) must not be blocked by the incomplete run.
    finishing = _finishing_llm(final_message="Real report.")
    messenger2 = FakeMessenger()
    ran2 = _review(conn, tmp_path, finishing, now=now, force=False, messenger=messenger2, manner="scheduled")
    assert ran2 is True
    assert messenger2.sent == ["Real report."]


def test_manual_run_with_no_questions_sends_immediately(conn, tmp_path):
    now = datetime(2026, 1, 8, 9, 0, tzinfo=TZ)
    messenger = FakeMessenger()
    llm = _finishing_llm(final_message="Nothing to flag this week.")

    _review(conn, tmp_path, llm, now=now, force=True, messenger=messenger, manner="manual")

    assert messenger.sent == ["Nothing to flag this week."]
    assert weekly_qa.get(conn, CHAT_ID, now=now) is None


def test_every_report_is_persisted_even_when_delivery_is_held(conn, tmp_path):
    """A manual run with questions holds the report back until the Q&A is answered —
    and /skip may mean it is never sent at all. Store it either way: the deep-dive
    export is built from these, and a report the user skipped is still analysis."""
    now = datetime(2026, 1, 8, 9, 0, tzinfo=TZ)
    messenger = FakeMessenger()
    llm = _finishing_llm(final_message="Fibre was low.", questions=["How was your energy?"])

    _review(conn, tmp_path, llm, now=now, force=True, messenger=messenger, manner="manual")

    assert "Fibre was low." not in messenger.sent          # held for the Q&A
    stored = weekly_reports.since(conn, "0000-01-01")
    assert len(stored) == 1
    assert stored[0]["report"] == "Fibre was low."
    assert stored[0]["questions"] == ["How was your energy?"]
    assert stored[0]["manner"] == "manual"


def test_incomplete_run_stores_no_report(conn, tmp_path):
    now = datetime(2026, 1, 8, 17, 0, tzinfo=TZ)
    llm = FakeLLM([
        LLMResponse(data={"thought": "t", "tool": "read_diet", "args_json": "{}",
                          "final_message": None, "questions": []})
        for _ in range(30)
    ])

    _review(conn, tmp_path, llm, now=now, force=True, manner="scheduled")

    assert weekly_reports.since(conn, "0000-01-01") == []


# --- not asking the same thing every week ------------------------------------------

WEEK = timedelta(days=7)


def test_question_already_asked_last_week_is_dropped_and_report_sent_at_once(conn, tmp_path):
    """The agent re-asked the same hunger question every week. A repeat is filtered in
    code, and with nothing left a manual run has nothing to hold the report for."""
    last = datetime(2026, 1, 1, 9, 0, tzinfo=TZ)
    weekly_reports.save(conn, "old", manner="scheduled", questions=["Are you often hungry?"], now=last)
    now = last + WEEK
    messenger = FakeMessenger()
    llm = _finishing_llm(final_message="This week.", questions=["Do you often feel hungry?"])

    _review(conn, tmp_path, llm, now=now, force=True, messenger=messenger, manner="manual")

    assert messenger.sent == ["This week."]
    assert weekly_qa.get(conn, CHAT_ID, now=now) is None
    assert weekly_reports.since(conn, "2026-01-05")[0]["questions"] == []


def test_new_question_still_gets_through(conn, tmp_path):
    last = datetime(2026, 1, 1, 9, 0, tzinfo=TZ)
    weekly_reports.save(conn, "old", manner="scheduled", questions=["Are you often hungry?"], now=last)
    now = last + WEEK
    messenger = FakeMessenger()
    llm = _finishing_llm(final_message="This week.", questions=["Did the knee injury heal?"])

    _review(conn, tmp_path, llm, now=now, force=True, messenger=messenger, manner="scheduled")

    assert weekly_qa.get(conn, CHAT_ID, now=now).questions == ["Did the knee injury heal?"]


def test_drop_repeats_caps_at_two_and_dedupes_within_the_list(conn):
    now = datetime(2026, 1, 8, 9, 0, tzinfo=TZ)
    asked = weekly_reports.drop_repeats(conn, [
        "How is your sleep lately?",
        "How has your sleep been lately?",       # same question reworded
        "Did your knee injury heal?",
        "Any new supplements started?",
        "Has your commute changed recently?",
    ], now=now)
    assert asked == ["How is your sleep lately?", "Did your knee injury heal?"]


def test_question_older_than_the_window_may_be_asked_again(conn):
    old = datetime(2026, 1, 1, 9, 0, tzinfo=TZ)
    weekly_reports.save(conn, "old", manner="scheduled", questions=["Are you often hungry?"], now=old)
    later = old + timedelta(days=weekly_reports.REPEAT_WINDOW_DAYS + 2)
    assert weekly_reports.drop_repeats(conn, ["Are you often hungry?"], now=later) == [
        "Are you often hungry?"
    ]


def test_answers_are_stored_with_the_questions_and_shown_to_the_agent(conn):
    now = datetime(2026, 1, 8, 9, 0, tzinfo=TZ)
    weekly_reports.save(conn, "r", manner="scheduled", questions=["Are you often hungry?"], now=now)
    assert "unanswered" in weekly_reports.format_history(conn, now=now)

    weekly_reports.record_answers(conn, ["No, I feel full most days"])

    history = weekly_reports.format_history(conn, now=now)
    assert "Are you often hungry?" in history and "feel full most days" in history
    assert weekly_reports.format_history(conn, now=now + timedelta(weeks=20)).startswith("No questions")


def test_finishing_a_qa_files_the_answers_on_the_report(conn, tmp_path):
    from xirtun.pipeline.intake import handle_message

    now = datetime(2026, 1, 8, 9, 0, tzinfo=TZ)
    weekly_reports.save(conn, "r", manner="scheduled", questions=["Feeling hungry often?"], now=now)
    weekly_qa.start(conn, CHAT_ID, mode="capture", questions=["Feeling hungry often?"], now=now)
    weekly_qa.record_answer(conn, CHAT_ID, weekly_qa.get(conn, CHAT_ID, now=now), "not really", now=now)

    handle_message("done", chat_id=CHAT_ID, llm=FakeLLM(), conn=conn, messenger=FakeMessenger(),
                   observations_path=tmp_path / "o.md", now=now)

    assert weekly_reports.since(conn, "2026-01-01")[0]["answers"] == ["not really"]


def test_agent_can_call_get_past_questions(conn, tmp_path):
    from xirtun.agent import tools

    now = datetime(2026, 1, 8, 9, 0, tzinfo=TZ)
    weekly_reports.save(conn, "r", manner="scheduled", questions=["Is the ankle better?"], now=now)
    ctx = tools.ToolContext(conn=conn, diet_path=tmp_path / "d.md",
                            observations_path=tmp_path / "o.md", now=now)
    assert "Is the ankle better?" in tools.build_dispatch(ctx)["get_past_questions"]({})


def test_old_database_gains_the_answers_column(tmp_path):
    import sqlite3

    from xirtun.storage import db

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE weekly_reports (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, "
        "manner TEXT NOT NULL, report TEXT NOT NULL, questions TEXT NOT NULL DEFAULT '[]')"
    )
    old.commit()
    old.close()

    db.init_db(path)

    conn = db.get_connection(path)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(weekly_reports)")}
    assert "answers" in cols
