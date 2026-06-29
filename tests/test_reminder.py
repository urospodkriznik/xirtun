"""Tests for the morning weight-log reminder."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from xirtun import targets
from xirtun.messaging.fake import FakeMessenger
from xirtun.run_reminder import send_weight_reminder
from xirtun.storage import runs

TZ = timezone.utc


def _onboarded(tmp_path: Path) -> Path:
    """A non-empty diet.md so the reminder isn't suppressed as pre-onboarding."""
    diet_path = tmp_path / "diet.md"
    diet_path.write_text("Vegan. Hiatal hernia.\n")
    return diet_path


def _mark_review(conn, when: datetime) -> None:
    rid = runs.start(conn, when)
    runs.finish(conn, rid, when, "ok")


def _remind(conn, diet_path, messenger, *, now):
    return send_weight_reminder(
        conn=conn, diet_path=diet_path, messenger=messenger, tz=TZ, now=now,
    )


def test_reminds_when_due_and_weight_stale(conn, tmp_path):
    now = datetime(2026, 1, 8, 8, 0, tzinfo=TZ)
    _mark_review(conn, now - timedelta(days=6, hours=15))  # review due by tonight
    messenger = FakeMessenger()

    assert _remind(conn, _onboarded(tmp_path), messenger, now=now) is True
    assert len(messenger.sent) == 1


def test_silent_when_weight_recent(conn, tmp_path):
    now = datetime(2026, 1, 8, 8, 0, tzinfo=TZ)
    _mark_review(conn, now - timedelta(days=6, hours=15))
    targets.update_weight(conn, 82.0, now=now - timedelta(days=2))  # logged recently
    messenger = FakeMessenger()

    assert _remind(conn, _onboarded(tmp_path), messenger, now=now) is False
    assert messenger.sent == []


def test_silent_when_review_not_due(conn, tmp_path):
    now = datetime(2026, 1, 8, 8, 0, tzinfo=TZ)
    _mark_review(conn, now - timedelta(days=2))  # ran two days ago, nowhere near due
    messenger = FakeMessenger()

    assert _remind(conn, _onboarded(tmp_path), messenger, now=now) is False
    assert messenger.sent == []


def test_silent_before_onboarding(conn, tmp_path):
    now = datetime(2026, 1, 8, 8, 0, tzinfo=TZ)
    # No diet.md written and no prior review (first-run due), but still no nudge.
    messenger = FakeMessenger()

    assert _remind(conn, tmp_path / "diet.md", messenger, now=now) is False
    assert messenger.sent == []
