"""Gemini provider (experimental: not yet run against a live key). Function calling forced to the one tool."""

import importlib
import time
from typing import Any

from ailr.exceptions import LLMError
from ailr.llm.base import CallMetadata, LLMClient, ToolSchema
from ailr.llm.retry import with_retries


def _transport_errors() -> tuple[type[Exception], ...]:
    """Connection and timeout errors from whichever of the two HTTP libraries the SDK is using."""
    found = []
    for name in ("httpx", "httpx2"):
        try:
            found.append(importlib.import_module(name).TransportError)
        except (ImportError, AttributeError):
            pass
    return tuple(found)


class GeminiClient(LLMClient):
    def __init__(
        self,
        *,
        model: str,
        max_retries: int = 3,
        api_key: str | None = None,
    ) -> None:
        try:
            from google import genai
            from google.genai import errors, types
        except ImportError as e:
            raise LLMError("google-genai SDK not installed. Run: pip install ailr[gemini]") from e
        self._errors, self._types = errors, types
        self._transport_errors = _transport_errors()
        # The SDK retries only when given retry options, so with_retries stays the one retry layer.
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._max_retries = max_retries

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._model

    def complete_structured(
        self,
        *,
        system: str,
        user_message: str,
        tool_schema: ToolSchema,
        max_tokens: int = 4096,
        cache_system: bool = True,  # Gemini caches implicitly; flag accepted for interface parity
    ) -> tuple[dict[str, Any], CallMetadata]:
        errors, types = self._errors, self._types
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            tools=[types.Tool(function_declarations=[types.FunctionDeclaration(
                name=tool_schema.name,
                description=tool_schema.description,
                # plain JSON Schema, so ailr's schemas need no translation into Gemini's own Schema type
                parameters_json_schema=tool_schema.input_schema,
            )])],
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
                mode="ANY", allowed_function_names=[tool_schema.name],
            )),
        )

        def call():
            return self._client.models.generate_content(model=self._model, contents=user_message, config=config)

        def is_retryable(e: Exception) -> bool:
            if isinstance(e, errors.APIError):
                return isinstance(e.code, int) and (e.code in (408, 429) or e.code >= 500)
            return isinstance(e, self._transport_errors)

        t0 = time.monotonic()
        try:
            response = with_retries(call, is_retryable=is_retryable, max_retries=self._max_retries)
        except Exception as e:
            raise LLMError(f"Gemini call failed: {e}") from e
        latency_ms = int((time.monotonic() - t0) * 1000)

        usage = response.usage_metadata
        meta = CallMetadata(
            provider="gemini",
            model=self._model,
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            # Gemini counts thinking apart from the answer; the other providers include it in output.
            output_tokens=(getattr(usage, "candidates_token_count", 0) or 0)
            + (getattr(usage, "thoughts_token_count", 0) or 0),
            cached_input_tokens=getattr(usage, "cached_content_token_count", 0) or 0,
            latency_ms=latency_ms,
        )

        if not response.candidates:
            reason = getattr(getattr(response.prompt_feedback, "block_reason", None), "name", None)
            raise LLMError(f"Gemini returned no answer (prompt blocked: {reason}).", metadata=meta)
        candidate = response.candidates[0]
        finish_reason = getattr(candidate.finish_reason, "name", None)
        if finish_reason == "MAX_TOKENS":
            raise LLMError(
                f"The response hit the {max_tokens}-token output limit and was cut off, so it is incomplete.",
                metadata=meta,
            )
        for part in (candidate.content.parts if candidate.content else None) or []:
            if part.function_call is not None and part.function_call.name == tool_schema.name:
                return dict(part.function_call.args or {}), meta
        raise LLMError(
            f"No function call named {tool_schema.name!r} in Gemini response (finish reason: {finish_reason}).",
            metadata=meta,
        )
