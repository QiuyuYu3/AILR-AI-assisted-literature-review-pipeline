"""Anthropic provider. Uses tool_use for structured output and ephemeral cache for system prompts."""

import time
from typing import Any, Optional

from ailr.exceptions import LLMError
from ailr.llm.base import CallMetadata, LLMClient, ToolSchema
from ailr.llm.retry import with_retries


class AnthropicClient(LLMClient):
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
            import anthropic
        except ImportError as e:
            raise LLMError(
                "anthropic SDK not installed. Run: pip install ailr[anthropic]"
            ) from e

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._temperature = temperature
        self._seed = seed
        self._max_retries = max_retries

    @property
    def provider_name(self) -> str:
        return "anthropic"

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
        cache_system: bool = True,
    ) -> tuple[dict[str, Any], CallMetadata]:
        anthropic = self._anthropic

        if cache_system:
            system_param: Any = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_param = system

        tools_param = [
            {
                "name": tool_schema.name,
                "description": tool_schema.description,
                "input_schema": tool_schema.input_schema,
            }
        ]

        def call():
            return self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                temperature=self._temperature,
                system=system_param,
                tools=tools_param,
                tool_choice={"type": "tool", "name": tool_schema.name},
                messages=[{"role": "user", "content": user_message}],
            )

        def is_retryable(e: Exception) -> bool:
            if isinstance(e, (anthropic.RateLimitError, anthropic.APITimeoutError)):
                return True
            if isinstance(e, anthropic.APIStatusError):
                return e.status_code is not None and e.status_code >= 500
            if isinstance(e, anthropic.InternalServerError):
                return True
            return False

        t0 = time.monotonic()
        try:
            response = with_retries(
                call,
                is_retryable=is_retryable,
                max_retries=self._max_retries,
            )
        except anthropic.AuthenticationError as e:
            raise LLMError(f"Anthropic authentication failed: {e}") from e
        except anthropic.BadRequestError as e:
            raise LLMError(f"Anthropic rejected request: {e}") from e
        except Exception as e:
            raise LLMError(f"Anthropic call failed: {e}") from e

        latency_ms = int((time.monotonic() - t0) * 1000)

        tool_use_block = None
        for block in response.content:
            if block.type == "tool_use" and block.name == tool_schema.name:
                tool_use_block = block
                break
        if tool_use_block is None:
            raise LLMError(
                f"No tool_use block named {tool_schema.name!r} in response. "
                f"Got blocks: {[b.type for b in response.content]}"
            )

        usage = response.usage
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0

        meta = CallMetadata(
            provider="anthropic",
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cache_read,
            cache_creation_tokens=cache_write,
            latency_ms=latency_ms,
        )

        return dict(tool_use_block.input), meta
