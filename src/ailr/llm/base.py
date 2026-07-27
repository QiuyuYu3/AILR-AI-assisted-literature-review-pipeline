"""Provider-agnostic LLM interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ToolSchema:
    """A structured-output contract. Internally JSON Schema; adapters translate to provider format."""
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class CallMetadata:
    """Per-call telemetry. Persisted to api_calls table.

    Token counts only. ailr deliberately does not estimate spend: per-token prices change
    faster than the package ships, and a stale price table reads as authoritative while
    being wrong. Multiply these counts by your provider's current rates instead.
    """
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_creation_tokens: int = 0
    latency_ms: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


# Providers whose API accepts a seed. The rest silently ignore whatever the config says, so the
# answer lives here only: nothing downstream may claim a seed the call did not actually send.
_SEED_PROVIDERS = frozenset({"openai"})


def effective_seed(provider: str, seed: Optional[int]) -> Optional[int]:
    """The seed the call really used, or None when the provider has no such parameter."""
    return seed if provider in _SEED_PROVIDERS else None


class LLMClient(ABC):
    """Abstract LLM client. All providers implement complete_structured().

    Concrete clients must enforce the tool_schema at the provider level — no free-text
    JSON parsing. They must also populate CallMetadata with token counts.
    """

    @property
    def effective_seed(self) -> Optional[int]:
        """None unless the provider sends a seed. Overridden by the providers that do."""
        return None

    @abstractmethod
    def complete_structured(
        self,
        *,
        system: str,
        user_message: str,
        tool_schema: ToolSchema,
        max_tokens: int = 4096,
        cache_system: bool = True,
    ) -> tuple[dict[str, Any], CallMetadata]:
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        ...
