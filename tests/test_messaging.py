"""Tests for the Telegram messaging helpers — pure, deterministic, no network.

``parse_update`` and ``next_offset`` are pure functions extracted from the transport
so they can be tested directly.
"""

from datetime import datetime, timezone

from xirtun.messaging.telegram import next_offset, parse_update

SAMPLE_UPDATE = {
    "update_id": 100,
    "message": {
        "message_id": 5,
        "date": 1_700_000_000,
        "chat": {"id": 4242},
        "text": "hi",
    },
}


def test_parse_update_text():
    msg = parse_update(SAMPLE_UPDATE)
    assert msg is not None
    assert msg.sender_id == "4242"
    assert msg.text == "hi"
    assert msg.timestamp == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)


def test_parse_update_ignores_non_text():
    assert parse_update({"update_id": 7}) is None
    assert parse_update({"update_id": 7, "message": {"chat": {}}}) is None


def test_next_offset_empty_batch_unchanged():
    assert next_offset([], 50) == 50


def test_next_offset_advances_past_highest():
    updates = [{"update_id": 100}, {"update_id": 103}, {"update_id": 101}]
    assert next_offset(updates, 0) == 104
