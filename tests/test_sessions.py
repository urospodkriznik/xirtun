"""Tests for pending-meal session state, using an injected clock (no wall-clock)."""

from datetime import datetime, timedelta, timezone

from xirtun.pipeline import sessions

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def test_upsert_and_get_round_trip(conn):
    sessions.upsert(conn, "c1", "meal", "curry", now=T0)
    s = sessions.get_active(conn, "c1", now=T0)
    assert s is not None
    assert s.kind == "meal"
    assert s.text == "curry"


def test_expired_session_is_cleared(conn):
    sessions.upsert(conn, "c1", "meal", "curry", now=T0)
    later = T0 + timedelta(minutes=31)
    assert sessions.get_active(conn, "c1", now=later) is None
    # an unexpired lookup afterward should also find nothing — it was deleted
    assert sessions.get_active(conn, "c1", now=T0) is None


def test_clear(conn):
    sessions.upsert(conn, "c1", "meal", "x", now=T0)
    sessions.clear(conn, "c1")
    assert sessions.get_active(conn, "c1", now=T0) is None
