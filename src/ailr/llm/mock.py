"""MockLLMClient: deterministic, no-API client for development and CI."""

from typing import Any, Callable, Optional

from ailr.llm.base import CallMetadata, LLMClient, ToolSchema


def synth_from_tool_schema(tool_schema: ToolSchema) -> dict[str, Any]:
    """Build a schema-shaped fake response so Mock extraction populates every field
    (value + quote leaves, repeating groups, nested objects, _flag_check) — lets you
    test the whole extraction UI end-to-end without an API call."""
    return _synth_object(tool_schema.input_schema or {}, None)


def _synth_object(schema: dict, parent: Optional[str]) -> dict:
    return {n: _synth_value(n, s, parent) for n, s in (schema.get("properties") or {}).items()}


def _synth_value(name: str, sub: dict, parent: Optional[str]) -> Any:
    if sub.get("enum"):
        return sub["enum"][0]
    t = sub.get("type")
    if isinstance(t, list):  # nullable types like ["string", "null"]
        t = next((x for x in t if x != "null"), "string")
    if t == "object":
        return _synth_object(sub, name)
    if t == "array":
        return [_synth_value(name, sub.get("items") or {}, parent)]
    if t in ("integer", "number"):
        return 5 if "confidence" in name.lower() else 1
    if t == "boolean":
        return False
    low = name.lower()
    if low in ("quote", "source_quote"):
        return "Mock supporting quote from the paper."
    if low == "value":
        return f"Mock {parent or 'value'}"
    if low == "reason":
        return "Mock rationale."
    return f"Mock {name}"


class MockLLMClient(LLMClient):
    """Returns a canned response for every call. Use for end-to-end pipeline tests
    without burning tokens.

    Two ways to drive responses:
      1. Pass `response` (a single dict) — same response every call.
      2. Pass `response_fn` (a callable) — receives (system, user_message, tool_schema)
         and returns the dict. Useful for varying responses by input.
    """

    def __init__(
        self,
        *,
        model: str = "mock",
        response: Optional[dict[str, Any]] = None,
        response_fn: Optional[Callable[[str, str, ToolSchema], dict[str, Any]]] = None,
        latency_ms: int = 0,
    ) -> None:
        self._model = model
        self._default_response = response or {
            "decision": "uncertain",
            "reasoning": "Mock client - real reviewer needed.",
            "matched_criteria": [],
            "evidence_quotes": [],
            "confidence": 5,
        }
        self._response_fn = response_fn
        self._latency_ms = latency_ms
        self.call_count = 0
        self.calls: list[dict[str, Any]] = []

    @property
    def provider_name(self) -> str:
        return "mock"

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
        self.call_count += 1
        self.calls.append(
            {
                "system_len": len(system),
                "user_message_len": len(user_message),
                "tool_schema_name": tool_schema.name,
            }
        )

        if self._response_fn is not None:
            output = self._response_fn(system, user_message, tool_schema)
        else:
            output = dict(self._default_response)

        meta = CallMetadata(
            provider="mock",
            model=self._model,
            input_tokens=(len(system) + len(user_message)) // 4,
            output_tokens=128,
            latency_ms=self._latency_ms,
            cost_estimate=0.0,
        )
        return output, meta
