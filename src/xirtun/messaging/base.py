"""The messaging contract.

`TelegramMessenger` (and a future `WhatsAppMessenger`) satisfy `Messenger`. The
rest of the app depends only on `Messenger` + `IncomingMessage` and never imports
a provider library — that's what makes the transport swappable (and testable via
`FakeMessenger`).

``IncomingMessage`` is a typed, provider-neutral container for one inbound message;
``raw`` keeps the original provider payload for debugging only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Protocol


@dataclass
class IncomingMessage:
    sender_id: str
    text: str
    timestamp: datetime
    raw: dict[str, Any]


class Messenger(Protocol):
    def send(self, text: str) -> None:
        """Send a text message to the (single) user."""
        ...

    def run(self, handler: Callable[[IncomingMessage], None]) -> None:
        """Start receiving. Blocks, calling `handler` for each inbound message."""
        ...
