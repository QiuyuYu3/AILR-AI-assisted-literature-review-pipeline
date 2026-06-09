"""Reviewers: abstraction, decision dataclasses, and the LLM-backed reviewer."""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional

from ailr.core.source import Source
from ailr.exceptions import LLMError
from ailr.extraction import FieldSpec, build_extraction_tool_schema, schema_to_markdown
from ailr.llm.base import CallMetadata, LLMClient, ToolSchema


@dataclass
class ScreeningDecision:
    """A single screening decision. One row in the screening_decisions table.

    `stage` distinguishes title/abstract screening from full-text re-verification.
    """
    decision: Literal["include", "exclude", "uncertain"]
    reasoning: str
    reviewer_type: str
    reviewer_id: str
    source_id: Optional[int] = None
    stage: Literal["abstract", "full_text"] = "abstract"
    evidence_quotes: list[str] = field(default_factory=list)
    matched_criteria: list[str] = field(default_factory=list)
    confidence: Optional[float] = None
    llm_params: Optional[dict[str, Any]] = None
    prompt_version: Optional[str] = None
    raw_output: Optional[str] = None
    timestamp: Optional[datetime] = None


@dataclass
class ExtractionResult:
    """One extracted field. One row in the extractions table."""
    extractor_type: str
    extractor_id: str
    field_name: str
    value: Any
    source_id: Optional[int] = None
    source_quote: Optional[str] = None
    page_or_section: Optional[str] = None
    confidence: Optional[float] = None
    is_newly_discovered: bool = False
    llm_params: Optional[dict[str, Any]] = None
    prompt_version: Optional[str] = None
    timestamp: Optional[datetime] = None


@dataclass
class SourceExtraction:
    """All extraction results for a single source from one reviewer pass."""
    source_id: int
    results: list[ExtractionResult] = field(default_factory=list)
    flag_check: Optional[list[dict[str, Any]]] = None
    raw_output: Optional[dict[str, Any]] = None


class Reviewer(ABC):
    """Abstract reviewer. Either an LLM client wrapper or a human's identity tag."""

    @property
    @abstractmethod
    def reviewer_type(self) -> str:
        """'ai' or 'human'."""

    @property
    @abstractmethod
    def reviewer_id(self) -> str:
        """Stable identifier (e.g., 'anthropic:claude-haiku-4-5-20251001' or 'alice@team')."""

    @abstractmethod
    def screen(
        self,
        source: Source,
        criteria_text: str,
        prompt_template: str,
    ) -> ScreeningDecision:
        """Make a screening decision for one source. Caller fills source_id afterward."""

    @abstractmethod
    def extract(
        self,
        source: Source,
        paper_text: str,
        fields: "list[FieldSpec]",
        prompt_template: str,
        criteria_text: str,
        *,
        with_quotes: bool = True,
        flag_check: bool = True,
    ) -> SourceExtraction:
        """Extract structured fields from the full-text paper. Caller fills source_id afterward."""


SCREENING_TOOL = ToolSchema(
    name="record_screening_decision",
    description="Record the screening decision for the abstract in the user message.",
    input_schema={
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["include", "exclude", "uncertain"],
                "description": "include / exclude / uncertain. Use uncertain when full-text review is needed.",
            },
            "reasoning": {
                "type": "string",
                "description": "1-2 sentences justifying the decision.",
            },
            "matched_criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": "IDs of criteria that influenced the decision.",
            },
            "evidence_quotes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Short verbatim quotes from the abstract supporting the decision.",
            },
            "confidence": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "Confidence 1-10.",
            },
        },
        "required": ["decision", "reasoning", "confidence"],
    },
)


class LLMReviewer(Reviewer):
    def __init__(
        self,
        llm_client: LLMClient,
        *,
        prompt_version: str = "v1",
        max_tokens: int = 2048,
        max_tokens_extract: int = 8192,
    ) -> None:
        self._client = llm_client
        self._prompt_version = prompt_version
        self._max_tokens = max_tokens
        self._max_tokens_extract = max_tokens_extract
        self.last_metadata: Optional[CallMetadata] = None

    @property
    def reviewer_type(self) -> str:
        return "ai"

    @property
    def reviewer_id(self) -> str:
        return f"{self._client.provider_name}:{self._client.model_name}"

    def screen(
        self,
        source: Source,
        criteria_text: str,
        prompt_template: str,
    ) -> ScreeningDecision:
        system_prompt = _render_system_prompt(prompt_template, criteria_text)
        user_message = _format_source_message(source)

        output, metadata = self._client.complete_structured(
            system=system_prompt,
            user_message=user_message,
            tool_schema=SCREENING_TOOL,
            max_tokens=self._max_tokens,
            cache_system=True,
        )
        self.last_metadata = metadata

        decision_value = output.get("decision")
        if decision_value not in ("include", "exclude", "uncertain"):
            raise LLMError(f"Invalid decision from LLM: {decision_value!r}")

        return ScreeningDecision(
            decision=decision_value,
            reasoning=output.get("reasoning", ""),
            reviewer_type=self.reviewer_type,
            reviewer_id=self.reviewer_id,
            evidence_quotes=output.get("evidence_quotes", []) or [],
            matched_criteria=output.get("matched_criteria", []) or [],
            confidence=output.get("confidence"),
            llm_params={
                "provider": metadata.provider,
                "model": metadata.model,
                "max_tokens": self._max_tokens,
            },
            prompt_version=self._prompt_version,
            raw_output=str(output),
        )


    def extract(
        self,
        source: Source,
        paper_text: str,
        fields: list[FieldSpec],
        prompt_template: str,
        criteria_text: str,
        *,
        with_quotes: bool = True,
        flag_check: bool = True,
    ) -> SourceExtraction:
        tool_schema = build_extraction_tool_schema(
            fields,
            with_quotes=with_quotes,
            tool_name="record_extraction",
            tool_description="Record the structured extraction for the paper in the user message.",
        )
        if flag_check:
            tool_schema = _add_flag_check_to_schema(tool_schema)

        schema_md = schema_to_markdown(fields)
        system_prompt = (
            prompt_template
            .replace("{{schema_md}}", schema_md)
            .replace("{{schema_json}}", schema_md)  # legacy alias
            .replace("{{criteria}}", criteria_text)
        )
        # Drop any leftover {{...}} placeholders so they don't leak literally into the prompt.
        # (The paper text is sent as a separate user message, so {{paper_text}} is intentionally dropped.)
        system_prompt = re.sub(r"\{\{[^}]+\}\}", "", system_prompt)

        user_message = _format_paper_message(source, paper_text)

        output, metadata = self._client.complete_structured(
            system=system_prompt,
            user_message=user_message,
            tool_schema=tool_schema,
            max_tokens=self._max_tokens_extract,
            cache_system=True,
        )
        self.last_metadata = metadata

        results: list[ExtractionResult] = []
        for field in fields:
            if field.name not in output:
                continue
            raw = output[field.name]
            value, quote = _unwrap_value_quote(raw, with_quotes=with_quotes, field=field)
            results.append(
                ExtractionResult(
                    extractor_type=self.reviewer_type,
                    extractor_id=self.reviewer_id,
                    field_name=field.name,
                    value=value,
                    source_quote=quote,
                    llm_params={
                        "provider": metadata.provider,
                        "model": metadata.model,
                    },
                    prompt_version=self._prompt_version,
                )
            )

        return SourceExtraction(
            source_id=source.id or 0,
            results=results,
            flag_check=output.get("_flag_check") if flag_check else None,
            raw_output=output,
        )


def _render_system_prompt(template: str, criteria_text: str) -> str:
    return template.replace("{{criteria}}", criteria_text)


def _format_paper_message(source: Source, paper_text: str) -> str:
    header_parts: list[str] = [f"Title: {source.title}"]
    if source.year:
        header_parts.append(f"Year: {source.year}")
    if source.doi:
        header_parts.append(f"DOI: {source.doi}")
    header = "\n".join(header_parts)
    return f"{header}\n\n--- FULL TEXT ---\n\n{paper_text}"


def _add_flag_check_to_schema(tool_schema: ToolSchema) -> ToolSchema:
    """Inject a _flag_check array property for inclusion-criterion re-verification."""
    schema = dict(tool_schema.input_schema)
    props = dict(schema.get("properties", {}))
    props["_flag_check"] = {
        "type": "array",
        "description": "Re-verify each inclusion criterion against the full text. One item per criterion.",
        "items": {
            "type": "object",
            "properties": {
                "criterion_id": {"type": "string", "description": "Criterion identifier (e.g. B1, Population)"},
                "verdict": {"type": "string", "enum": ["PASS", "FAIL", "UNCERTAIN"]},
                "reason": {"type": "string", "description": "One sentence."},
                "confidence": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["criterion_id", "verdict", "reason"],
        },
    }
    schema["properties"] = props
    return ToolSchema(
        name=tool_schema.name,
        description=tool_schema.description,
        input_schema=schema,
    )


def _unwrap_value_quote(raw: Any, *, with_quotes: bool, field: FieldSpec) -> tuple[Any, Optional[str]]:
    if not with_quotes:
        return raw, None
    if field.type in ("string", "integer", "number", "boolean"):
        if isinstance(raw, dict) and "value" in raw:
            return raw.get("value"), raw.get("quote")
        return raw, None
    # Object / list: keep full structure; quotes live at leaves inside.
    return raw, None


def _format_source_message(source: Source) -> str:
    parts: list[str] = [f"Title: {source.title}"]
    if source.year:
        parts.append(f"Year: {source.year}")
    if source.authors:
        shown = "; ".join(source.authors[:5])
        if len(source.authors) > 5:
            shown += " et al."
        parts.append(f"Authors: {shown}")
    if source.abstract:
        parts.append("")
        parts.append("Abstract:")
        parts.append(source.abstract)
    else:
        parts.append("")
        parts.append("Abstract: (not available)")
    return "\n".join(parts)
