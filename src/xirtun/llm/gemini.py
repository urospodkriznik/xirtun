"""Gemini adapter for the LLMClient contract.

The only module that depends on google-genai. Provider-specific translation is
isolated here so the pipeline and agent stay provider-agnostic (docs/decisions.md
ADR-005): supporting another provider means adding a sibling adapter, not touching
callers.

Structured output: a Pydantic model is passed as ``schema``, Gemini fills it, and we
return the validated data as a plain dict in ``LLMResponse.data``.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from google import genai
from google.genai import errors, types

from xirtun.llm.base import LLMResponse

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 2.0


class GeminiClient:
    def __init__(self, api_key: str, model: str, *, temperature: float = 0.0) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._temperature = temperature

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        schema: type | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        if tools is not None:
            raise NotImplementedError("tool calling arrives in the weekly-run slice")

        # Gemini separates the system instruction from the conversation turns.
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [
            types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part(text=m["content"])],
            )
            for m in messages
            if m["role"] != "system"
        ]

        config = types.GenerateContentConfig(
            system_instruction=system or None,
            temperature=self._temperature,
            response_mime_type="application/json" if schema else None,
            response_schema=schema,
        )

        response = self._generate(contents, config)

        data: dict[str, Any] | None = None
        if schema is not None:
            # When a schema is given, response.parsed is a populated Pydantic
            # instance; fall back to parsing the raw JSON text if it isn't set.
            if response.parsed is not None:
                data = response.parsed.model_dump()
            else:
                data = json.loads(response.text)

        return LLMResponse(text=response.text or "", data=data)

    def _generate(self, contents: Any, config: Any) -> Any:
        """Call Gemini, retrying transient errors (5xx, 429) with backoff."""
        delay = _RETRY_BACKOFF_SECONDS
        for attempt in range(_MAX_RETRIES):
            try:
                return self._client.models.generate_content(
                    model=self._model, contents=contents, config=config
                )
            except (errors.ServerError, errors.ClientError) as exc:
                code = getattr(exc, "code", None)
                retryable = isinstance(exc, errors.ServerError) or code == 429
                if not retryable or attempt == _MAX_RETRIES - 1:
                    raise
                logger.warning("Gemini transient error (%s); retrying in %.0fs", code, delay)
                time.sleep(delay)
                delay *= 2
