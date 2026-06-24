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
import random
import time
from typing import Any

import httpx
from google import genai
from google.genai import errors, types

from xirtun.llm.base import LLMResponse

logger = logging.getLogger(__name__)

_MAX_RETRIES = 5
_RETRY_BACKOFF_SECONDS = 2.0
# Fail a hung request fast (instead of waiting ~60s) so a retry gets a real chance.
_REQUEST_TIMEOUT_MS = 30_000


class GeminiClient:
    def __init__(self, api_key: str, model: str, *, temperature: float = 0.0) -> None:
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=_REQUEST_TIMEOUT_MS),
        )
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

    def transcribe(self, audio_bytes: bytes, mime_type: str) -> str:
        """Transcribe an audio clip to plain text (used for Telegram voice notes)."""
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part(text="Transcribe this audio to plain text. Return only the transcript."),
                    types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
                ],
            )
        ]
        response = self._generate(contents, types.GenerateContentConfig(temperature=0.0))
        return (response.text or "").strip()

    def _generate(self, contents: Any, config: Any) -> Any:
        """Call Gemini, retrying transient errors (5xx, 429, network drops) with backoff."""
        delay = _RETRY_BACKOFF_SECONDS
        for attempt in range(_MAX_RETRIES):
            try:
                return self._client.models.generate_content(
                    model=self._model, contents=contents, config=config
                )
            except (errors.ServerError, errors.ClientError, httpx.RequestError) as exc:
                code = getattr(exc, "code", None)
                # 5xx and network/transport drops (e.g. "server disconnected") are
                # transient; 429 is rate-limiting. Other 4xx are not retryable.
                retryable = isinstance(exc, (errors.ServerError, httpx.RequestError)) or code == 429
                if not retryable or attempt == _MAX_RETRIES - 1:
                    raise
                sleep_for = delay + random.uniform(0, 1)  # jitter to spread out retries
                logger.warning(
                    "Gemini transient error (%s); retrying in %.1fs", type(exc).__name__, sleep_for
                )
                time.sleep(sleep_for)
                delay *= 2
