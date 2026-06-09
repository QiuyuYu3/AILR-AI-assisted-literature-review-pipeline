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
    """Per-call telemetry. Persisted to api_calls table."""
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_creation_tokens: int = 0
    latency_ms: int = 0
    cost_estimate: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


class LLMClient(ABC):
    """Abstract LLM client. All providers implement complete_structured().

    Concrete clients must enforce the tool_schema at the provider level — no free-text
    JSON parsing. They must also populate CallMetadata with token counts and cost.
    """

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
