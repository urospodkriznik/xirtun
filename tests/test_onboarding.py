"""Tests for the first-run interview and the onboard-vs-intake dispatch."""

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


def test_dispatch_skips_onboarding_when_diet_exists(conn, tmp_path):
    diet_path = tmp_path / "diet.md"
    diet_path.write_text("# Profile\n- vegan\n")
    llm = FakeLLM([LLMResponse(data={"intent": "other"})])  # normal intake path
    messenger = FakeMessenger()

    dispatch("how are you?", chat_id="c1", llm=llm, conn=conn, messenger=messenger, diet_path=diet_path)

    assert messenger.sent  # handled by intake (classify=other), not onboarding


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
