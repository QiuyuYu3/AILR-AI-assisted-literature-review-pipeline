"""Factory: provider name -> concrete LLMClient instance.

API keys are read from the process environment by each provider's SDK
(ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY). Export them in your shell
before launching; nothing is read from or written to disk.
"""

from typing import Optional

from ailr.exceptions import ConfigError
from ailr.llm.base import LLMClient


def make_llm_client(
    provider: str,
    *,
    model: str,
    temperature: float = 0.0,
    seed: Optional[int] = 42,
    max_retries: int = 3,
    api_key: Optional[str] = None,
) -> LLMClient:
    if provider == "mock":
        from ailr.llm.mock import MockLLMClient
        return MockLLMClient(model=model)

    if provider == "anthropic":
        from ailr.llm.providers.anthropic import AnthropicClient
        return AnthropicClient(
            model=model,
            temperature=temperature,
            seed=seed,
            max_retries=max_retries,
            api_key=api_key,
        )

    if provider == "openai":
        from ailr.llm.providers.openai import OpenAIClient
        return OpenAIClient(
            model=model,
            temperature=temperature,
            seed=seed,
            max_retries=max_retries,
            api_key=api_key,
        )

    if provider == "gemini":
        from ailr.llm.providers.gemini import GeminiClient
        return GeminiClient(
            model=model,
            temperature=temperature,
            seed=seed,
            max_retries=max_retries,
            api_key=api_key,
        )

    raise ConfigError(
        f"Unknown LLM provider: {provider!r}. Supported: anthropic, openai, gemini, mock."
    )
