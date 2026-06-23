"""A fake messenger for tests.

Records everything "sent" so tests can assert on outgoing messages, and lets a
test "inject" an incoming message to drive the handler — without any network.
"""

from __future__ import annotations

from typing import Callable

from xirtun.messaging.base import IncomingMessage


class FakeMessenger:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self._handler: Callable[[IncomingMessage], None] | None = None

    def send(self, text: str) -> None:
        self.sent.append(text)

    def run(self, handler: Callable[[IncomingMessage], None]) -> None:
        # Unlike the real messenger, we don't block here — we just remember the
        # handler so a test can call inject() to simulate an inbound message.
        self._handler = handler

    def inject(self, message: IncomingMessage) -> None:
        if self._handler is None:
            raise AssertionError("inject() called before run() registered a handler")
        self._handler(message)
