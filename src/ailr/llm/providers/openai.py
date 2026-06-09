"""OpenAI provider. Uses function calling (tools) for structured output."""

import json
import time
from typing import Any, Optional

from ailr.exceptions import LLMError
from ailr.llm.base import CallMetadata, LLMClient, ToolSchema
from ailr.llm.retry import with_retries


class OpenAIClient(LLMClient):
    def __init__(
        self,
        *,
        model: str,
        temperature: float = 0.0,
        seed: Optional[int] = 42,
        max_retries: int = 3,
        api_key: Optional[str] = None,
    ) -> None:
        try:
            import openai
        except ImportError as e:
            raise LLMError("openai SDK not installed. Run: pip install ailr[openai]") from e
        self._openai = openai
        self._client = openai.OpenAI(api_key=api_key)
        self._model = model
        self._temperature = temperature
        self._seed = seed
        self._max_retries = max_retries

    @property
    def provider_name(self) -> str:
        return "openai"

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
        cache_system: bool = True,  # OpenAI caches automatically; flag accepted for interface parity
    ) -> tuple[dict[str, Any], CallMetadata]:
        openai = self._openai
        tools = [
            {
                "type": "function",
                "function": {
                    "name": tool_schema.name,
                    "description": tool_schema.description,
                    "parameters": tool_schema.input_schema,
                },
            }
        ]

        def call():
            return self._client.chat.completions.create(
                model=self._model,
                temperature=self._temperature,
                seed=self._seed,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_message},
                ],
                tools=tools,
                tool_choice={"type": "function", "function": {"name": tool_schema.name}},
            )

        def is_retryable(e: Exception) -> bool:
            if isinstance(e, (openai.RateLimitError, openai.APITimeoutError, openai.InternalServerError)):
                return True
            if isinstance(e, openai.APIStatusError):
                return getattr(e, "status_code", None) is not None and e.status_code >= 500
            return False

        t0 = time.monotonic()
        try:
            response = with_retries(call, is_retryable=is_retryable, max_retries=self._max_retries)
        except openai.AuthenticationError as e:
            raise LLMError(f"OpenAI authentication failed: {e}") from e
        except openai.BadRequestError as e:
            raise LLMError(f"OpenAI rejected request: {e}") from e
        except Exception as e:
            raise LLMError(f"OpenAI call failed: {e}") from e

        latency_ms = int((time.monotonic() - t0) * 1000)

        choice = response.choices[0]
        tool_calls = getattr(choice.message, "tool_calls", None)
        if not tool_calls:
            raise LLMError(f"No function call named {tool_schema.name!r} in OpenAI response.")
        try:
            output = json.loads(tool_calls[0].function.arguments)
        except (ValueError, TypeError) as e:
            raise LLMError(f"OpenAI returned invalid JSON arguments: {e}") from e

        usage = response.usage
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        cached = 0
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cached = getattr(details, "cached_tokens", 0) or 0

        meta = CallMetadata(
            provider="openai",
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached,
            latency_ms=latency_ms,
        )
        return output, meta
