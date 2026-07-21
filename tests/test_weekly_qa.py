"""Tests for the weekly Q&A resume flow: capturing answers to a review's
calibrating questions, resuming after a meal/symptom interrupts it, and the
'cancel' escape hatch for a stuck clarification loop (see intake.py, weekly_qa.py).
"""

from datetime import datetime, timezone

from xirtun.llm.base import LLMResponse
from xirtun.llm.fake import FakeLLM
from xirtun.messaging.fake import FakeMessenger
from xirtun.pipeline import sessions, weekly_qa
from xirtun.pipeline.intake import dispatch, handle_message
from xirtun.storage import diary

CHAT_ID = "c1"
NOW = datetime(2026, 1, 8, 9, 0, tzinfo=timezone.utc)


def _start_capture(conn, questions=("Feeling hungry often?",)):
    weekly_qa.start(conn, CHAT_ID, mode="capture", questions=list(questions), now=NOW)


def _start_interactive(conn, report="Your week in review.", questions=("Feeling hungry often?",)):
    weekly_qa.start(conn, CHAT_ID, mode="interactive", questions=list(questions), report=report, now=NOW)


def test_capture_mode_records_answer_and_finalizes_on_done(conn, tmp_path):
    obs = tmp_path / "observations.md"
    _start_capture(conn)
    messenger = FakeMessenger()

    # Context-aware router says this is an answer, not a new log.
    handle_message("yes, quite hungry actually", chat_id=CHAT_ID,
                   llm=FakeLLM([LLMResponse(data={"kind": "answer"})]),
                   conn=conn, messenger=messenger, observations_path=obs, now=NOW)
    assert weekly_qa.get(conn, CHAT_ID, now=NOW) is not None   # still open

    handle_message("done", chat_id=CHAT_ID, llm=FakeLLM(), conn=conn, messenger=messenger,
                   observations_path=obs, now=NOW)

    assert weekly_qa.get(conn, CHAT_ID, now=NOW) is None       # closed
    assert "hungry actually" in obs.read_text()
    assert "Feeling hungry often?" in obs.read_text()


def test_interactive_mode_holds_report_until_skip(conn, tmp_path):
    obs = tmp_path / "observations.md"
    _start_interactive(conn)
    messenger = FakeMessenger()

    handle_message("skip", chat_id=CHAT_ID, llm=FakeLLM(), conn=conn, messenger=messenger,
                   observations_path=obs, now=NOW)

    assert "Your week in review." in messenger.sent
    assert weekly_qa.get(conn, CHAT_ID, now=NOW) is None
    assert "skipped without answering" in obs.read_text()


def test_meal_logged_mid_qa_is_processed_and_qa_resumes(conn, tmp_path):
    """Logging a meal while weekly_qa is pending should log it AND re-show the open
    question — this is the resume-on-interrupt behavior."""
    obs = tmp_path / "observations.md"
    _start_interactive(conn)
    messenger = FakeMessenger()

    llm = FakeLLM([
        LLMResponse(data={"kind": "new_log"}),       # context-aware router: unrelated new entry
        LLMResponse(data={"intent": "meal"}),        # then the plain classifier routes it
        LLMResponse(data={"needs_clarification": False, "meals": [
            {"occurred_at": None, "notes": None, "items": [{"name": "banana", "calories": 90}]},
        ]}),
    ])
    handle_message("I ate a banana", chat_id=CHAT_ID, llm=llm, conn=conn, messenger=messenger,
                   observations_path=obs, now=NOW)

    assert any("banana" in m for m in messenger.sent)               # meal was logged
    assert any("Feeling hungry often?" in m for m in messenger.sent)  # question resurfaced
    qa = weekly_qa.get(conn, CHAT_ID, now=NOW)
    assert qa is not None and qa.report == "Your week in review."     # not lost
    assert conn.execute("SELECT COUNT(*) AS n FROM meals").fetchone()["n"] == 1


def test_answer_discussing_symptoms_is_not_logged_as_a_symptom(conn, tmp_path):
    """Regression (prod bug 2026-07-20): the review asked about bloating/appetite; the
    user's answer naturally discussed bloating, so the plain classifier logged it as a
    symptom instead of recording it as an answer. The context-aware router must keep it
    an answer — nothing should land in the symptoms table."""
    obs = tmp_path / "observations.md"
    weekly_qa.start(conn, CHAT_ID, mode="capture",
                    questions=["How are your meal volumes / appetite?"], now=NOW)
    messenger = FakeMessenger()

    reply = ("i kind of don't feel hungry, no appetite, but my belly still feels bloated — "
             "maybe smoothies would help, and not working out is a big factor")
    handle_message(reply, chat_id=CHAT_ID, llm=FakeLLM([LLMResponse(data={"kind": "answer"})]),
                   conn=conn, messenger=messenger, observations_path=obs, now=NOW)

    assert conn.execute("SELECT COUNT(*) AS n FROM symptoms").fetchone()["n"] == 0
    assert weekly_qa.get(conn, CHAT_ID, now=NOW) is not None      # still collecting answers

    handle_message("done", chat_id=CHAT_ID, llm=FakeLLM(), conn=conn, messenger=messenger,
                   observations_path=obs, now=NOW)
    assert "smoothies would help" in obs.read_text()             # captured as an answer


def test_qa_survives_an_unrelated_undo_command(conn, tmp_path):
    """The whole point of the dedicated table: a pending Q&A must survive an unrelated
    command that uses the shared `pending` slot. /undo (open undo_confirm → 'yes') used
    to clobber the Q&A, so the later answer fell through and got logged as a symptom."""
    obs = tmp_path / "observations.md"
    diet = tmp_path / "diet.md"
    diet.write_text("# Profile")
    diary.save_meal(conn, "banana",
                    {"occurred_at": None, "notes": None, "items": [{"name": "banana", "calories": 90}]})
    weekly_qa.start(conn, CHAT_ID, mode="capture", questions=["How's your appetite?"], now=NOW)

    handle_message("/undo", chat_id=CHAT_ID, llm=FakeLLM(), conn=conn, messenger=FakeMessenger(),
                   diet_path=diet, observations_path=obs, now=NOW)
    handle_message("yes", chat_id=CHAT_ID, llm=FakeLLM(), conn=conn, messenger=FakeMessenger(),
                   diet_path=diet, observations_path=obs, now=NOW)

    assert conn.execute("SELECT COUNT(*) AS n FROM meals").fetchone()["n"] == 0  # undo worked
    assert weekly_qa.get(conn, CHAT_ID, now=NOW) is not None                     # Q&A survived

    handle_message("no appetite, feeling bloated", chat_id=CHAT_ID,
                   llm=FakeLLM([LLMResponse(data={"kind": "answer"})]),
                   conn=conn, messenger=FakeMessenger(), observations_path=obs, now=NOW)
    assert conn.execute("SELECT COUNT(*) AS n FROM symptoms").fetchone()["n"] == 0  # not a symptom

    handle_message("done", chat_id=CHAT_ID, llm=FakeLLM(), conn=conn, messenger=FakeMessenger(),
                   observations_path=obs, now=NOW)
    assert "feeling bloated" in obs.read_text()                                  # captured as answer


def test_real_command_runs_while_qa_pending(conn, tmp_path):
    """A genuine command (/today) mid-Q&A must run, not be swallowed as an answer —
    and the Q&A stays open afterward."""
    _start_capture(conn)
    messenger = FakeMessenger()
    handle_message("/today", chat_id=CHAT_ID, llm=FakeLLM(), conn=conn, messenger=messenger, now=NOW)

    assert "No meals logged today" in messenger.sent[-1]         # /today ran
    assert weekly_qa.get(conn, CHAT_ID, now=NOW) is not None     # Q&A untouched


def test_weekly_command_declines_while_qa_pending(conn, tmp_path):
    """Re-running /weekly mid-Q&A would silently overwrite (and lose) the held
    report/answers-so-far — it should decline instead."""
    diet = tmp_path / "diet.md"
    diet.write_text("# Profile")
    _start_interactive(conn)
    messenger = FakeMessenger()
    called = []

    dispatch("/weekly", chat_id=CHAT_ID, llm=FakeLLM(), conn=conn, messenger=messenger,
              diet_path=diet, weekly_cb=lambda: called.append(True), now=NOW)

    assert called == []
    assert "still got questions open" in messenger.sent[-1]
    qa = weekly_qa.get(conn, CHAT_ID, now=NOW)
    assert qa is not None and qa.report == "Your week in review."   # untouched


def test_cancel_word_escapes_stuck_symptom_clarification(conn, tmp_path):
    sessions.upsert(conn, CHAT_ID, "symptom", "I feel weird and off somehow", now=NOW)
    messenger = FakeMessenger()

    handle_message("cancel", chat_id=CHAT_ID, llm=FakeLLM(), conn=conn, messenger=messenger, now=NOW)

    assert sessions.get_active(conn, CHAT_ID, now=NOW) is None
    assert "Cancelled" in messenger.sent[-1]
    assert conn.execute("SELECT COUNT(*) AS n FROM symptoms").fetchone()["n"] == 0
