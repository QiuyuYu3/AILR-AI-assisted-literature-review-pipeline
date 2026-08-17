"""LLM cross-checker: a second model judging an extraction that already exists.

Not a Reviewer. It never produces a value of its own — it is given the recorded value and the
quote offered as support, and returns a verdict per field. Because its input contains the record
it judges, it is not independent of it and its verdicts stay out of agreement statistics.
"""

import json
from importlib.resources import files
from pathlib import Path
from typing import Any, Optional

from ailr.core.crosscheck_screening import DECISION_FIELD
from ailr.core.source import Source
from ailr.exceptions import LLMError
from ailr.extraction import FieldSpec, compose_prompt, schema_to_markdown
from ailr.llm.base import CallMetadata, LLMClient, ToolSchema


VERDICTS = ("agree", "disagree", "uncertain")

BUILT_IN_PROMPT = "crosscheck_prompt.txt"
BUILT_IN_SCREENING_PROMPT = "crosscheck_screening_prompt.txt"
# Truncated so one oversized field cannot crowd the paper out of the context window.
_MAX_VALUE_CHARS = 2000


def load_prompt(
    project_root: Path,
    rel_path: str = "prompts/crosscheck.txt",
    built_in: str = BUILT_IN_PROMPT,
) -> str:
    """The project's prompt if it has one, else the built-in default."""
    path = Path(project_root) / rel_path
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if text.strip():
            return text
    return (files("ailr") / built_in).read_text(encoding="utf-8")


def compose_crosscheck_prompt(
    template: str,
    *,
    project_name: str = "",
    schema_md: str = "",
    additional: str = "",
) -> str:
    """Same {{additional}} handling as the other stages: appended when a hand-written template
    has no marker, so a project prompt written before this existed still picks it up."""
    has_marker = "{{additional}}" in (template or "")
    composed = compose_prompt(
        template, project_name=project_name, schema_md=schema_md, additional=additional
    )
    if additional and additional.strip() and not has_marker:
        composed = composed.rstrip() + "\n\n# ADDITIONAL INSTRUCTIONS\n\n" + additional.strip()
    return composed


def compose_screening_crosscheck_prompt(
    template: str,
    *,
    project_name: str = "",
    criteria: str = "",
    additional: str = "",
) -> str:
    has_marker = "{{additional}}" in (template or "")
    composed = compose_prompt(
        template, project_name=project_name, criteria=criteria, additional=additional
    )
    if additional and additional.strip() and not has_marker:
        composed = composed.rstrip() + "\n\n# ADDITIONAL INSTRUCTIONS\n\n" + additional.strip()
    return composed


# One verdict per decision, not per field: a screening record is a single judgement, so splitting
# it into slots the way an extraction is split would invent a structure that is not there.
SCREENING_CHECK_TOOL = ToolSchema(
    name="record_screening_cross_check",
    description="Record one verdict for the screening decision under review.",
    input_schema={
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": list(VERDICTS)},
            "reason": {"type": "string", "description": "One sentence."},
            "suggested_value": {
                "type": ["string", "null"],
                "description": "Only when the verdict is disagree: the decision the abstract supports "
                               "(include, exclude or uncertain). Null otherwise.",
            },
            "confidence": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["verdict", "reason", "suggested_value", "confidence"],
    },
)


def format_decision_message(source: Source, decision: dict) -> str:
    """The record under review, then the title and abstract it was made from. No confidence and no
    reviewer identity: both anchor the checker onto the answer it is supposed to test."""
    quotes = [str(q) for q in (decision.get("evidence_quotes") or []) if str(q).strip()]
    matched = [str(c) for c in (decision.get("matched_criteria") or []) if str(c).strip()]

    lines = [
        "--- SCREENING DECISION UNDER REVIEW ---",
        "",
        f"decision: {decision.get('decision')}",
        f"reason: {decision.get('reasoning') or '(none given)'}",
        f"criteria cited: {', '.join(matched) if matched else '(none given)'}",
        "evidence quotes:",
        *([f"  - {q}" for q in quotes] or ["  (none given)"]),
    ]
    flag_check = decision.get("flag_check") or []
    if flag_check:
        lines.append("per-criterion verdicts:")
        lines.extend(
            f"  {i.get('criterion_id')}: {i.get('verdict')} — {i.get('reason') or ''}" for i in flag_check
        )

    header = [f"Title: {source.title}"]
    if source.year:
        header.append(f"Year: {source.year}")
    abstract = getattr(source, "abstract", "") or "(no abstract)"
    return (
        "\n".join(header) + "\n\n" + "\n".join(lines)
        + "\n\n--- TITLE AND ABSTRACT ---\n\n" + f"{source.title}\n\n{abstract}"
    )


class ScreeningCrossChecker:
    """A second model judging a screening decision that already exists. Same standing as the
    extraction checker: advisory, never a reviewer, never in the agreement statistics."""

    def __init__(self, llm_client: LLMClient, *, prompt_version: str = "v1", max_tokens: int = 1024) -> None:
        self._client = llm_client
        self._prompt_version = prompt_version
        self._max_tokens = max_tokens
        self._last_metadata: Optional[CallMetadata] = None

    @property
    def checker_id(self) -> str:
        return f"{self._client.provider_name}:{self._client.model_name}"

    @property
    def prompt_version(self) -> str:
        return self._prompt_version

    @property
    def last_metadata(self) -> Optional[CallMetadata]:
        return self._last_metadata

    def llm_params(self) -> dict[str, Any]:
        meta = self._last_metadata
        params: dict[str, Any] = {
            "provider": meta.provider if meta else self._client.provider_name,
            "model": meta.model if meta else self._client.model_name,
            "temperature": self._client.temperature,
            "max_tokens": self._max_tokens,
        }
        seed = self._client.effective_seed
        if seed is not None:
            params["seed"] = seed
        return params

    def check(
        self,
        source: Source,
        decision: dict,
        prompt_template: str,
        *,
        project_name: str = "",
        criteria: str = "",
        additional: str = "",
    ) -> dict[str, dict]:
        """The verdict, keyed by field name so it stores through the same path as the extraction
        checker's per-field verdicts."""
        system_prompt = compose_screening_crosscheck_prompt(
            prompt_template, project_name=project_name, criteria=criteria, additional=additional
        )
        output, metadata = self._client.complete_structured(
            system=system_prompt,
            user_message=format_decision_message(source, decision),
            tool_schema=SCREENING_CHECK_TOOL,
            max_tokens=self._max_tokens,
            cache_system=True,
        )
        self._last_metadata = metadata

        verdict = str(output.get("verdict") or "").lower()
        if verdict not in VERDICTS:
            raise LLMError(f"Invalid screening cross-check verdict: {output.get('verdict')!r}")
        return {
            DECISION_FIELD: {
                "verdict": verdict,
                "reason": output.get("reason") or "",
                "suggested_value": output.get("suggested_value"),
                "confidence": output.get("confidence"),
                "raw": output,
            }
        }


def build_tool_schema(field_names: list[str]) -> ToolSchema:
    """One named slot per field, each required: the model can neither skip a field nor invent one."""
    def slot() -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": list(VERDICTS)},
                "reason": {"type": "string", "description": "One sentence."},
                "suggested_value": {
                    "type": ["string", "null"],
                    "description": "Only when the verdict is disagree and the paper states a different value.",
                },
                "confidence": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["verdict", "reason", "suggested_value", "confidence"],
        }

    return ToolSchema(
        name="record_cross_check",
        description="Record one verdict for every field of the extraction under review.",
        input_schema={
            "type": "object",
            "properties": {name: slot() for name in field_names},
            "required": list(field_names),
        },
    )


def _render_value(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > _MAX_VALUE_CHARS:
        text = text[:_MAX_VALUE_CHARS] + " …(truncated)"
    return text


def format_record_message(source: Source, rows: list[dict], paper_text: str) -> str:
    """The record under review first, then the paper. No confidence or reasoning from the original
    extractor: seeing those anchors the checker onto the answer it is supposed to test."""
    header = [f"Title: {source.title}"]
    if source.year:
        header.append(f"Year: {source.year}")
    if source.doi:
        header.append(f"DOI: {source.doi}")

    lines = ["--- EXTRACTION UNDER REVIEW ---", ""]
    for row in rows:
        lines.append(f"## {row['field_name']}")
        lines.append(f"value: {_render_value(row.get('value'))}")
        quote = row.get("source_quote")
        lines.append(f"quote: {quote}" if quote else "quote: (none given)")
        lines.append("")

    return "\n".join(header) + "\n\n" + "\n".join(lines) + "\n--- FULL TEXT ---\n\n" + paper_text


class LLMCrossChecker:
    def __init__(self, llm_client: LLMClient, *, prompt_version: str = "v1", max_tokens: int = 8000) -> None:
        self._client = llm_client
        self._prompt_version = prompt_version
        self._max_tokens = max_tokens
        self._last_metadata: Optional[CallMetadata] = None

    @property
    def checker_id(self) -> str:
        return f"{self._client.provider_name}:{self._client.model_name}"

    @property
    def prompt_version(self) -> str:
        return self._prompt_version

    @property
    def last_metadata(self) -> Optional[CallMetadata]:
        return self._last_metadata

    def llm_params(self) -> dict[str, Any]:
        meta = self._last_metadata
        params: dict[str, Any] = {
            "provider": meta.provider if meta else self._client.provider_name,
            "model": meta.model if meta else self._client.model_name,
            "temperature": self._client.temperature,
            "max_tokens": self._max_tokens,
        }
        seed = self._client.effective_seed
        if seed is not None:
            params["seed"] = seed
        return params

    def check(
        self,
        source: Source,
        paper_text: str,
        rows: list[dict],
        fields: list[FieldSpec],
        prompt_template: str,
        *,
        project_name: str = "",
        additional: str = "",
    ) -> dict[str, dict]:
        """Verdicts keyed by field name, for the fields present in `rows`."""
        field_names = [r["field_name"] for r in rows]
        if not field_names:
            return {}

        system_prompt = compose_crosscheck_prompt(
            prompt_template,
            project_name=project_name,
            schema_md=schema_to_markdown(fields),
            additional=additional,
        )
        output, metadata = self._client.complete_structured(
            system=system_prompt,
            user_message=format_record_message(source, rows, paper_text),
            tool_schema=build_tool_schema(field_names),
            max_tokens=self._max_tokens,
            cache_system=True,
        )
        self._last_metadata = metadata

        verdicts: dict[str, dict] = {}
        for name in field_names:
            item = output.get(name)
            if not isinstance(item, dict):
                continue
            verdict = str(item.get("verdict") or "").lower()
            if verdict not in VERDICTS:
                raise LLMError(f"Invalid cross-check verdict for {name!r}: {item.get('verdict')!r}")
            verdicts[name] = {
                "verdict": verdict,
                "reason": item.get("reason") or "",
                "suggested_value": item.get("suggested_value"),
                "confidence": item.get("confidence"),
                "raw": item,
            }
        return verdicts
