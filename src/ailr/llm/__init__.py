"""LLM abstraction layer.

Public surface:
    - LLMClient: provider-agnostic interface (ABC)
    - CallMetadata: per-call usage + cost + latency record
    - ToolSchema: provider-agnostic JSON-Schema-based output contract
    - make_llm_client: factory mapping provider name -> concrete client

Concrete providers live in `ailr.llm.providers.*` and are loaded lazily by the factory
so their SDK dependencies are only required when actually used.
"""

from ailr.llm.base import CallMetadata, LLMClient, ToolSchema
from ailr.llm.factory import make_llm_client

__all__ = ["CallMetadata", "LLMClient", "ToolSchema", "make_llm_client"]
