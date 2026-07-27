"""Gemini provider (EXPERIMENTAL — untested without an API key).

Uses google-generativeai function calling forced via tool_config mode=ANY.
The JSON-schema-to-Gemini-schema path and proto arg parsing may need adjustment
against a live key; report errors and we'll refine.
"""

import time
from typing import Any, Optional

from ailr.exceptions import LLMError
from ailr.llm.base import CallMetadata, LLMClient, ToolSchema
from ailr.llm.retry import with_retries


# Errors that will fail identically on every attempt: a wrong key or a schema the API rejects
# does not become valid by waiting. Anything else is assumed transient and still gets retried,
# so an error this list has not seen behaves as it did before rather than failing fast.
_PERMANENT_MARKERS = (
    "api key", "api_key", "unauthenticated", "permission",
    "invalid argument", "invalid_argument", "400",
)


def _is_retryable(e: Exception) -> bool:
    """Whether another attempt could plausibly succeed. google-generativeai has no stable exception
    hierarchy across versions (the other two providers use isinstance on their SDK's classes), so
    this reads the message. Retrying a bad key 3 times with backoff costs ~7s per call, which on a
    batch run is hours of sleeping before the user learns the key is wrong."""
    msg = str(e).lower()
    return not any(m in msg for m in _PERMANENT_MARKERS)


def _proto_to_py(obj: Any) -> Any:
    """Recursively convert Gemini proto Map/Repeated composites into plain Python."""
    if obj is None or isinstance(obj, (str, bytes, bool, int, float)):
        return obj
    if hasattr(obj, "items"):  # MapComposite / dict-like
        return {k: _proto_to_py(v) for k, v in obj.items()}
    if hasattr(obj, "__iter__"):  # RepeatedComposite / list-like
        return [_proto_to_py(v) for v in obj]
    return obj


class GeminiClient(LLMClient):
    def __init__(
        self,
        *,
        model: str,
        temperature: float = 0.0,
        max_retries: int = 3,
        api_key: Optional[str] = None,
    ) -> None:
        try:
            import google.generativeai as genai
        except ImportError as e:
            raise LLMError("google-generativeai SDK not installed. Run: pip install ailr[gemini]") from e
        self._genai = genai
        if api_key:
            genai.configure(api_key=api_key)
        self._model = model
        self._temperature = temperature
        self._max_retries = max_retries

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def temperature(self) -> Optional[float]:
        return self._temperature

    def complete_structured(
        self,
        *,
        system: str,
        user_message: str,
        tool_schema: ToolSchema,
        max_tokens: int = 4096,
        cache_system: bool = True,
    ) -> tuple[dict[str, Any], CallMetadata]:
        genai = self._genai
        tool = {
            "function_declarations": [
                {
                    "name": tool_schema.name,
                    "description": tool_schema.description,
                    "parameters": tool_schema.input_schema,
                }
            ]
        }
        model = genai.GenerativeModel(self._model, system_instruction=system, tools=[tool])

        def call():
            return model.generate_content(
                user_message,
                generation_config={"temperature": self._temperature, "max_output_tokens": max_tokens},
                tool_config={"function_calling_config": {"mode": "ANY", "allowed_function_names": [tool_schema.name]}},
            )

        t0 = time.monotonic()
        try:
            response = with_retries(call, is_retryable=_is_retryable, max_retries=self._max_retries)
        except Exception as e:
            raise LLMError(f"Gemini call failed: {e}") from e
        latency_ms = int((time.monotonic() - t0) * 1000)

        output: Optional[dict] = None
        try:
            for part in response.candidates[0].content.parts:
                fc = getattr(part, "function_call", None)
                if fc is not None and getattr(fc, "name", None) == tool_schema.name:
                    output = _proto_to_py(fc.args)
                    break
        except (AttributeError, IndexError) as e:
            raise LLMError(f"Could not parse Gemini function call: {e}") from e
        if output is None:
            raise LLMError(f"No function call named {tool_schema.name!r} in Gemini response.")

        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", 0) or 0 if usage else 0
        output_tokens = getattr(usage, "candidates_token_count", 0) or 0 if usage else 0
        meta = CallMetadata(
            provider="gemini",
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
        )
        return dict(output), meta
