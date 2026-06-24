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


def test_voice_update_transcribed(monkeypatch):
    from xirtun.messaging.telegram import TelegramMessenger

    messenger = TelegramMessenger(
        token="t", chat_id="1", conn=None, transcribe=lambda data, mime: "two eggs and toast"
    )
    monkeypatch.setattr(messenger, "_download_file", lambda file_id: b"audio")
    update = {
        "update_id": 1,
        "message": {"date": 1_700_000_000, "chat": {"id": 4242},
                    "voice": {"file_id": "abc", "mime_type": "audio/ogg"}},
    }

    msg = messenger._to_message(update)

    assert msg is not None
    assert msg.text == "two eggs and toast"
    assert msg.sender_id == "4242"


def test_voice_without_transcriber_ignored():
    from xirtun.messaging.telegram import TelegramMessenger

    messenger = TelegramMessenger(token="t", chat_id="1", conn=None)  # no transcriber
    update = {"update_id": 1, "message": {"date": 1_700_000_000, "chat": {"id": 4242},
                                          "voice": {"file_id": "abc"}}}
    assert messenger._to_message(update) is None
