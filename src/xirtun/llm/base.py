"""The LLM contract.

Every provider (Gemini, OpenAI, Groq, ...) will be an adapter class that satisfies
`LLMClient`. Tests use `FakeLLM`, which also satisfies it. The pipeline and the
agent depend only on this contract — never on a provider SDK directly.

``LLMClient`` is a structural interface (``typing.Protocol``): any class with a
matching ``complete`` method satisfies it, with no inheritance or registration. That
is what lets the real provider client and the test fake be used interchangeably.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass
class LLMResponse:
    text: str = ""                       # free-text reply, when the model just talks
    data: dict[str, Any] | None = None   # parsed JSON, when a `schema` was requested
    tool_call: ToolCall | None = None    # set when the model wants to call a tool


class LLMClient(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        schema: type | None = None,            # a Pydantic BaseModel subclass
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Send `messages` to the model.

        - `schema` set  -> provider returns JSON validated to it -> `LLMResponse.data`.
        - `tools` set   -> provider may return a `LLMResponse.tool_call`.
        - neither       -> plain text in `LLMResponse.text`.
        """
        ...
