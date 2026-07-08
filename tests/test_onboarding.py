"""Tests for the first-run interview and the onboard-vs-intake dispatch."""

from zoneinfo import ZoneInfo

from xirtun.llm.base import LLMResponse
from xirtun.llm.fake import FakeLLM
from xirtun.memory import diet as memory
from xirtun.messaging.fake import FakeMessenger
from xirtun.pipeline.intake import dispatch


def test_onboarding_asks_then_writes_diet(conn, tmp_path):
    diet_path = tmp_path / "diet.md"
    llm = FakeLLM([
        LLMResponse(data={"done": False, "question": "Any food allergies?"}),
        LLMResponse(data={"done": True, "diet_markdown": "# Profile\n- No allergies\n"}),
    ])
    messenger = FakeMessenger()

    # First message -> interview begins; diet.md still empty.
    dispatch("hi", chat_id="c1", llm=llm, conn=conn, messenger=messenger, diet_path=diet_path)
    assert messenger.sent[-1] == "Any food allergies?"
    assert memory.is_empty(diet_path)

    # Answer -> profile written, onboarding complete.
    dispatch("none", chat_id="c1", llm=llm, conn=conn, messenger=messenger, diet_path=diet_path)
    assert not memory.is_empty(diet_path)
    assert "Profile" in diet_path.read_text()


def test_onboarding_stores_metrics(conn, tmp_path):
    from xirtun import targets
    diet = tmp_path / "diet.md"
    llm = FakeLLM([
        LLMResponse(data={
            "done": True,
            "diet_markdown": "# Profile",
            "metrics": {"sex": "male", "birth_year": 1994, "height_cm": 180, "weight_kg": 80, "activity": "moderate"},
        }),
    ])

    dispatch("I'm 30, male, 180cm, 80kg, moderate", chat_id="c1", llm=llm, conn=conn,
             messenger=FakeMessenger(), diet_path=diet)

    assert targets.read_metrics(conn)["weight_kg"] == 80


def test_onboarding_stores_timezone_separately_from_metrics(conn, tmp_path):
    from xirtun import targets
    from xirtun.storage import db

    diet = tmp_path / "diet.md"
    llm = FakeLLM([
        LLMResponse(data={
            "done": True,
            "diet_markdown": "# Profile",
            "metrics": {"sex": "male", "timezone": "Europe/Ljubljana"},
        }),
    ])

    dispatch("I live in Ljubljana", chat_id="c1", llm=llm, conn=conn,
             messenger=FakeMessenger(), diet_path=diet)

    assert db.get_timezone(conn, "default") == ZoneInfo("Europe/Ljubljana")
    assert "timezone" not in targets.read_metrics(conn)  # not a body metric


def test_onboarding_ignores_invalid_timezone(conn, tmp_path):
    from xirtun.storage import db

    diet = tmp_path / "diet.md"
    llm = FakeLLM([
        LLMResponse(data={
            "done": True,
            "diet_markdown": "# Profile",
            "metrics": {"sex": "male", "timezone": "Not/AZone"},
        }),
    ])

    dispatch("I live somewhere", chat_id="c1", llm=llm, conn=conn,
             messenger=FakeMessenger(), diet_path=diet)

    assert db.get_timezone(conn, "default") == "default"  # invalid value silently dropped


def test_dispatch_skips_onboarding_when_profile_is_current(conn, tmp_path):
    from xirtun.storage import db
    from xirtun.pipeline.onboarding_fields import CURRENT_ONBOARDING_VERSION

    diet_path = tmp_path / "diet.md"
    diet_path.write_text("# Profile\n- vegan\n")
    db.kv_set(conn, "onboarding_version", str(CURRENT_ONBOARDING_VERSION))  # already up to date
    llm = FakeLLM([LLMResponse(data={"intent": "other"})])  # normal intake path
    messenger = FakeMessenger()

    dispatch("how are you?", chat_id="c1", llm=llm, conn=conn, messenger=messenger, diet_path=diet_path)

    assert messenger.sent  # handled by intake (classify=other), not onboarding


def test_topup_runs_after_user_process_finishes(conn, tmp_path):
    from xirtun.storage import db
    from xirtun.pipeline.onboarding_fields import CURRENT_ONBOARDING_VERSION

    diet = tmp_path / "diet.md"
    diet.write_text("# Profile\n- vegan\n- age: 35\n")
    db.kv_set(conn, "onboarding_version", "1")  # a legacy v1 profile
    llm = FakeLLM([
        # /today uses no LLM; once the user is idle the top-up opens with a question...
        LLMResponse(data={"done": False, "question": "Where do you live most of the year?"}),
        # ...and their answer finishes it.
        LLMResponse(data={"done": True, "diet_markdown": "# Profile\n- vegan\n- Lives in: Spain\n"}),
    ])
    messenger = FakeMessenger()

    # A completed action (a report) leaves the user idle -> top-up offered right after.
    dispatch("/today", chat_id="c1", llm=llm, conn=conn, messenger=messenger, diet_path=diet)
    assert "Where do you live" in messenger.sent[-1]

    # The answer completes it: profile rewritten (stale 'age' dropped), version bumped.
    dispatch("Spain", chat_id="c1", llm=llm, conn=conn, messenger=messenger, diet_path=diet)
    assert "Spain" in diet.read_text()
    assert "age: 35" not in diet.read_text()
    assert db.kv_get(conn, "onboarding_version") == str(CURRENT_ONBOARDING_VERSION)


def test_topup_waits_until_a_pending_process_is_done(conn, tmp_path):
    """A meal mid-clarification must finish before the top-up appears."""
    from xirtun.storage import db
    from xirtun.pipeline import sessions

    diet = tmp_path / "diet.md"
    diet.write_text("# Profile\n- vegan\n")
    db.kv_set(conn, "onboarding_version", "1")
    llm = FakeLLM([
        LLMResponse(data={"intent": "meal"}),                                   # classify
        LLMResponse(data={"needs_clarification": True, "question": "How much?"}),  # meal asks back
    ])
    messenger = FakeMessenger()

    dispatch("some pasta", chat_id="c1", llm=llm, conn=conn, messenger=messenger, diet_path=diet)

    # The meal owns the session, so no top-up yet.
    assert messenger.sent[-1] == "How much?"
    assert sessions.get_active(conn, "c1").kind == "meal"


def test_topup_suspended_by_slash_command(conn, tmp_path):
    """A slash command during a top-up suspends it (version not bumped), the command
    runs normally, then the top-up restarts once the user is idle again."""
    from xirtun.storage import db
    from xirtun.pipeline.onboarding_fields import CURRENT_ONBOARDING_VERSION

    diet = tmp_path / "diet.md"
    diet.write_text("# Profile\n- vegan\n")
    db.kv_set(conn, "onboarding_version", "1")
    llm = FakeLLM([
        # first pass: top-up opens
        LLMResponse(data={"done": False, "question": "Where do you live most of the year?"}),
        # user sends /today → top-up is suspended; /today needs no LLM call
        # after /today the user is idle → top-up restarts
        LLMResponse(data={"done": False, "question": "Where do you live most of the year?"}),
        # user answers → top-up completes
        LLMResponse(data={"done": True, "diet_markdown": "# Profile\n- vegan\n- Lives in: Spain\n"}),
    ])
    messenger = FakeMessenger()

    dispatch("/today", chat_id="c1", llm=llm, conn=conn, messenger=messenger, diet_path=diet)
    # /today report came first, then the top-up question
    assert any("Where do you live" in m for m in messenger.sent)
    assert db.kv_get(conn, "onboarding_version") != str(CURRENT_ONBOARDING_VERSION)

    # User sends /today again while top-up is waiting — top-up must NOT consume it
    messenger.sent.clear()
    dispatch("/today", chat_id="c1", llm=llm, conn=conn, messenger=messenger, diet_path=diet)
    assert any("Where do you live" in m for m in messenger.sent)

    # Now the user answers the top-up question
    messenger.sent.clear()
    dispatch("Spain", chat_id="c1", llm=llm, conn=conn, messenger=messenger, diet_path=diet)
    assert db.kv_get(conn, "onboarding_version") == str(CURRENT_ONBOARDING_VERSION)


def test_topup_skip_defers_without_recording_version(conn, tmp_path):
    from xirtun.storage import db

    diet = tmp_path / "diet.md"
    diet.write_text("# Profile\n- vegan\n")
    db.kv_set(conn, "onboarding_version", "1")
    llm = FakeLLM([LLMResponse(data={"done": False, "question": "Where do you live most of the year?"})])
    messenger = FakeMessenger()

    dispatch("/today", chat_id="c1", llm=llm, conn=conn, messenger=messenger, diet_path=diet)  # opens the top-up
    dispatch("skip", chat_id="c1", llm=llm, conn=conn, messenger=messenger, diet_path=diet)    # defers it

    # Skip does NOT bump the version, so the top-up will return next time.
    assert db.kv_get(conn, "onboarding_version") == "1"


def test_clear_data_requires_confirm(conn, tmp_path):
    diet = tmp_path / "diet.md"
    diet.write_text("# Profile")
    messenger = FakeMessenger()

    dispatch("/cleardata", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger, diet_path=diet)

    assert "confirm" in messenger.sent[-1].lower()
    assert diet.exists()  # not wiped without confirmation


def test_clear_data_confirm_wipes(conn, tmp_path):
    diet = tmp_path / "diet.md"
    diet.write_text("# Profile")
    messenger = FakeMessenger()

    dispatch("/cleardata confirm", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger, diet_path=diet)

    assert not diet.exists()


def test_weekly_command_invokes_callback(conn, tmp_path):
    diet = tmp_path / "diet.md"
    diet.write_text("# Profile")
    messenger = FakeMessenger()
    called = []

    dispatch(
        "/weekly", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger,
        diet_path=diet, weekly_cb=lambda: called.append(True),
    )

    assert called == [True]
    assert messenger.sent  # acknowledged before running


def test_timezone_command_sets_kv_and_notifies_callback(conn, tmp_path):
    from xirtun.storage import db

    diet = tmp_path / "diet.md"
    diet.write_text("# Profile")
    messenger = FakeMessenger()
    changed = []

    dispatch(
        "/settimezone Europe/Ljubljana", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger,
        diet_path=diet, on_timezone_change=changed.append,
    )

    assert db.get_timezone(conn, "default") == ZoneInfo("Europe/Ljubljana")
    assert changed == [ZoneInfo("Europe/Ljubljana")]
    assert "Europe/Ljubljana" in messenger.sent[-1]


def test_timezone_command_rejects_invalid_zone(conn, tmp_path):
    from xirtun.storage import db

    diet = tmp_path / "diet.md"
    diet.write_text("# Profile")
    messenger = FakeMessenger()
    changed = []

    dispatch(
        "/settimezone Mars/Olympus", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger,
        diet_path=diet, on_timezone_change=changed.append,
    )

    assert db.get_timezone(conn, "default") == "default"  # unchanged
    assert changed == []  # callback not invoked
    assert "unknown" in messenger.sent[-1].lower()


def test_timezone_command_without_argument_shows_usage(conn, tmp_path):
    diet = tmp_path / "diet.md"
    diet.write_text("# Profile")
    messenger = FakeMessenger()

    dispatch("/settimezone", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger, diet_path=diet)

    assert "usage" in messenger.sent[-1].lower()


def test_timezone_command_works_before_onboarding(tmp_path, conn):
    """Like /cleardata and /weekly, /timezone should work even while diet.md is
    still empty — it's handled before the onboarding branch in dispatch()."""
    from xirtun.storage import db

    diet = tmp_path / "diet.md"  # empty -> would normally route to onboarding
    messenger = FakeMessenger()

    dispatch("/settimezone Europe/Ljubljana", chat_id="c1", llm=FakeLLM(), conn=conn, messenger=messenger, diet_path=diet)

    assert db.get_timezone(conn, "default") == ZoneInfo("Europe/Ljubljana")
    assert memory.is_empty(diet)  # onboarding wasn't touched
