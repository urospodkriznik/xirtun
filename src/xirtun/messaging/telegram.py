"""Telegram transport via the raw Bot API (no SDK), synchronous, long-polling.

It satisfies the Messenger protocol (send + run) without the rest of the app ever
knowing it's Telegram.

``_poll`` is a generator: it ``yield``s messages one at a time, so the long-poll
loop can run indefinitely and hand out messages as they arrive while ``run()``
iterates over it.

Offset / acknowledgement: Telegram delivers "updates" with an increasing
`update_id`. You acknowledge them by asking for `offset = highest_seen + 1` on the
next call — Telegram then drops the older ones server-side. We persist that offset
in the `kv` table so a restart resumes exactly where we left off.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from xirtun.messaging.base import IncomingMessage
from xirtun.storage import db

logger = logging.getLogger(__name__)

OFFSET_KEY = "tg_offset"
DEFAULT_API = "https://api.telegram.org"


# --- pure helpers (no network — easy to unit test) ---

def parse_update(update: dict[str, Any]) -> IncomingMessage | None:
    """Convert one Telegram 'update' into our neutral IncomingMessage.

    Returns None for updates that aren't a plain text message (edits, members
    joining, etc.) — v1 ignores those.
    """
    message = update.get("message")
    if not message or "text" not in message:
        return None
    return IncomingMessage(
        sender_id=str(message["chat"]["id"]),
        text=message["text"],
        timestamp=datetime.fromtimestamp(message["date"], tz=timezone.utc),
        raw=update,
    )


def next_offset(updates: list[dict[str, Any]], current: int) -> int:
    """Return the offset to request next time.

    The next offset is one past the highest update_id in this batch, or unchanged
    if the batch was empty.
    """
    # if `updates` is empty, return `current` unchanged. Otherwise return
    # (the largest update["update_id"] in the batch) + 1.
    if not updates:
        return current
    return max(u["update_id"] for u in updates) + 1


# --- the transport ---

class TelegramMessenger:
    def __init__(
        self,
        token: str,
        chat_id: str,
        conn,
        *,
        poll_timeout: int = 30,
        api: str = DEFAULT_API,
    ) -> None:
        self._chat_id = chat_id
        self._conn = conn
        self._poll_timeout = poll_timeout
        self._base = f"{api}/bot{token}"
        # The HTTP client's timeout must exceed the long-poll timeout, or the
        # client would give up before Telegram replies.
        self._client = httpx.Client(timeout=poll_timeout + 10)

    def send(self, text: str) -> None:
        resp = self._client.post(
            f"{self._base}/sendMessage",
            json={"chat_id": self._chat_id, "text": text},
        )
        resp.raise_for_status()  # turns a 4xx/5xx into an exception

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        """Register the slash-command menu shown in Telegram clients."""
        resp = self._client.post(
            f"{self._base}/setMyCommands",
            json={"commands": [{"command": name, "description": desc} for name, desc in commands]},
        )
        resp.raise_for_status()

    def _get_offset(self) -> int:
        raw = db.kv_get(self._conn, OFFSET_KEY)
        return int(raw) if raw else 0

    def _poll(self) -> Iterator[IncomingMessage]:
        while True:
            offset = self._get_offset()
            try:
                resp = self._client.get(
                    f"{self._base}/getUpdates",
                    params={"offset": offset, "timeout": self._poll_timeout},
                )
                resp.raise_for_status()
                updates = resp.json().get("result", [])
            except httpx.HTTPError as exc:
                # Network blip or Telegram hiccup: log and try again, don't crash.
                logger.warning("getUpdates failed, retrying: %s", exc)
                continue

            # Persist the new offset right after fetching: Telegram considers these
            # updates delivered once we request past them, so this matches its model.
            db.kv_set(self._conn, OFFSET_KEY, str(next_offset(updates, offset)))

            for update in updates:
                msg = parse_update(update)
                if msg is not None:
                    yield msg

    def run(self, handler: Callable[[IncomingMessage], None]) -> None:
        logger.info("Telegram long-poll started")
        for message in self._poll():
            try:
                handler(message)
            except Exception:                      # noqa: BLE001 — keep the bot alive
                logger.exception("handler failed for message from %s", message.sender_id)
                try:
                    self.send("Sorry — something went wrong on my end. Please try again in a moment.")
                except Exception:                  # noqa: BLE001 — best effort
                    logger.exception("failed to send error notice")
