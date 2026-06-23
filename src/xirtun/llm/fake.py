"""A scripted fake LLM for tests.

It returns pre-loaded responses in order, so a test can simulate a whole exchange
("model says: needs clarification" then "model says: here's the structured meal")
deterministically, with no network and no cost. It also records every call so
tests can assert what the model was asked.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from xirtun.llm.base import LLMResponse


class FakeLLM:
    def __init__(self, responses: list[LLMResponse] | None = None) -> None:
        self._queue: deque[LLMResponse] = deque(responses or [])
        self.calls: list[dict[str, Any]] = []

    def queue(self, *responses: LLMResponse) -> None:
        """Add responses that future complete() calls will return, in order."""
        self._queue.extend(responses)

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        schema: type | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        self.calls.append({"messages": messages, "schema": schema, "tools": tools})
        if not self._queue:
            raise AssertionError("FakeLLM.complete() called but no response was queued")
        return self._queue.popleft()
