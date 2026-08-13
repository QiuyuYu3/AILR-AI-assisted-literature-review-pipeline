"""Deterministic cross-checks: verify a recorded extraction against the paper text.

No LLM involved. Every check is a string or schema comparison, so running one is free and its
verdict is reproducible. These findings are advisory: they flag a record worth a second look,
they never block a submission.

Quote matching lives in ailr.quote_audit; this module turns its results, plus the schema
checks, into stored per-field findings.
"""

import json
from dataclasses import dataclass
from typing import Any, Optional

from ailr.extraction import FieldSpec
from ailr.quote_audit import (
    _PaperText,
    _collect_nested_quotes,
    _has_value,
    _normalize,
    _split_quote_cell,
)


QUOTE_NOT_FOUND = "quote_not_found"
VALUE_NOT_IN_QUOTE = "value_not_in_quote"
INVALID_ENUM = "invalid_enum"
EMPTY_REQUIRED = "empty_required"

# checker_id recorded for the deterministic layer, mirroring the "provider:model" shape the LLM
# layer writes so both read the same way in exports.
DETERMINISTIC_CHECKER = "ailr:deterministic"


@dataclass
class Issue:
    field_name: str
    issue_code: str
    reason: str


@dataclass
class CrossCheckRecord:
    """One row of the cross_checks table."""
    source_id: int
    stage: str
    target_type: str
    target_id: str
    checker_type: str
    checker_id: str
    check_kind: str
    verdict: str
    target_row_id: Optional[int] = None
    field_name: Optional[str] = None
    issue_code: Optional[str] = None
    reason: Optional[str] = None
    suggested_value: Optional[str] = None
    confidence: Optional[float] = None
    llm_params: Optional[dict] = None
    prompt_version: Optional[str] = None
    raw_output: Optional[str] = None


def _number_variants(value: Any) -> list[str]:
    """Ways the same number reads in prose, so a formatting difference is not a finding."""
    text = str(value).strip()
    out = [text]
    try:
        f = float(text)
    except (TypeError, ValueError):
        return out
    if f.is_integer() and str(int(f)) != text:
        out.append(str(int(f)))
    return out


def _truncate(text: str, width: int = 60) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[:width] + "..."


def _check_enum(field: FieldSpec, value: Any) -> Optional[Issue]:
    if not field.enum:
        return None
    allowed = {str(o).strip().lower() for o in field.enum}
    given = value if isinstance(value, list) else [value]
    bad = [v for v in given if isinstance(v, (str, int, float)) and str(v).strip().lower() not in allowed]
    if not bad:
        return None
    return Issue(
        field.name,
        INVALID_ENUM,
        f"Value {', '.join(repr(str(b)) for b in bad)} is not one of the allowed options.",
    )


def _check_quotes(field: FieldSpec, quotes: list[str], paper: _PaperText) -> Optional[Issue]:
    missing = [q for q in quotes if not paper.contains(q)]
    if not missing:
        return None
    suffix = f" (and {len(missing) - 1} more)" if len(missing) > 1 else ""
    return Issue(
        field.name,
        QUOTE_NOT_FOUND,
        f'Quote not found in the paper text: "{_truncate(missing[0])}"{suffix}',
    )


def _check_value_in_quote(field: FieldSpec, value: Any, quotes: list[str]) -> Optional[Issue]:
    """Scalar numbers only: a number appearing nowhere in its own supporting quote is the
    signature of a value carried over from elsewhere in the paper."""
    if field.type not in ("integer", "number") or not quotes:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    # Spacing and thousands separators differ freely between prose and a recorded number.
    haystack = _normalize(" ".join(quotes)).replace(",", "").replace(" ", "")
    if any(_normalize(v).replace(",", "").replace(" ", "") in haystack for v in _number_variants(value)):
        return None
    return Issue(
        field.name,
        VALUE_NOT_IN_QUOTE,
        f"Value {value!r} does not appear in its own supporting quote.",
    )


def check_extraction_row(row: dict, field: FieldSpec, paper: _PaperText) -> list[Issue]:
    value = row.get("value")
    if not _has_value(value):
        # An empty optional field is a legitimate "not reported", so only required ones are flagged.
        return [Issue(field.name, EMPTY_REQUIRED, "Required field has no value.")] if field.required else []

    issues: list[Issue] = []
    enum_issue = _check_enum(field, value)
    if enum_issue:
        issues.append(enum_issue)

    # Object and list-of-object values keep their quotes at the leaves rather than in the
    # source_quote cell, so both places have to be collected.
    quotes = _split_quote_cell(row.get("source_quote"))
    _collect_nested_quotes(value, quotes)
    if quotes:
        quote_issue = _check_quotes(field, quotes, paper)
        if quote_issue:
            issues.append(quote_issue)
        else:
            value_issue = _check_value_in_quote(field, value, quotes)
            if value_issue:
                issues.append(value_issue)
    return issues


def check_extraction(rows: list[dict], fields: list[FieldSpec], paper_text: str) -> list[Issue]:
    """Run every deterministic check over one extractor's rows for one source.

    `rows` are list_extractions() dicts (value already JSON-decoded); `fields` the schema they
    were extracted against. Rows whose field is not in the schema are skipped.
    """
    by_name = {f.name: f for f in fields}
    paper = _PaperText(paper_text)
    # A re-run appends rows rather than replacing them, so the last row per field is the live one.
    latest = {
        row.get("field_name"): row
        for row in rows
        if not str(row.get("field_name") or "").startswith("_")
    }

    issues: list[Issue] = []
    for name, row in latest.items():
        field = by_name.get(name)
        if field is not None:
            issues.extend(check_extraction_row(row, field, paper))
    for field in fields:
        if field.required and field.name not in latest:
            issues.append(Issue(field.name, EMPTY_REQUIRED, "Required field was not extracted."))
    return issues


def llm_verdicts_to_records(
    verdicts: dict[str, dict],
    *,
    source_id: int,
    target_type: str,
    target_id: str,
    row_ids: dict[str, int],
    checker_id: str,
    llm_params: Optional[dict] = None,
    prompt_version: Optional[str] = None,
    stage: str = "extraction",
) -> list["CrossCheckRecord"]:
    """LLM verdicts (keyed by field) as rows. Unlike the deterministic layer these carry no
    issue_code: the model's reason is the finding."""
    records: list[CrossCheckRecord] = []
    for field_name, v in verdicts.items():
        suggested = v.get("suggested_value")
        records.append(CrossCheckRecord(
            source_id=source_id,
            stage=stage,
            target_type=target_type,
            target_id=target_id,
            target_row_id=row_ids.get(field_name),
            field_name=field_name,
            checker_type="ai",
            checker_id=checker_id,
            check_kind="llm",
            verdict=v.get("verdict") or "uncertain",
            reason=v.get("reason") or None,
            suggested_value=None if suggested is None else str(suggested),
            confidence=v.get("confidence"),
            llm_params=llm_params,
            prompt_version=prompt_version,
            raw_output=json.dumps(v.get("raw"), ensure_ascii=False, default=str) if v.get("raw") else None,
        ))
    return records


def issues_to_records(
    issues: list[Issue],
    *,
    source_id: int,
    target_type: str,
    target_id: str,
    row_ids: dict[str, int],
    checked_fields: list[str],
    stage: str = "extraction",
) -> list[CrossCheckRecord]:
    """Findings become 'disagree' rows; every other checked field gets an explicit 'agree' row so
    a clean field is distinguishable from one that was never checked."""
    by_field: dict[str, list[Issue]] = {}
    for issue in issues:
        by_field.setdefault(issue.field_name, []).append(issue)

    def _make(field_name: str, verdict: str, issue: Optional[Issue]) -> CrossCheckRecord:
        return CrossCheckRecord(
            source_id=source_id,
            stage=stage,
            target_type=target_type,
            target_id=target_id,
            target_row_id=row_ids.get(field_name),
            field_name=field_name,
            checker_type="ai",
            checker_id=DETERMINISTIC_CHECKER,
            check_kind="deterministic",
            verdict=verdict,
            issue_code=issue.issue_code if issue else None,
            reason=issue.reason if issue else None,
        )

    records: list[CrossCheckRecord] = []
    for field_name in checked_fields:
        found = by_field.get(field_name)
        if found:
            records.extend(_make(field_name, "disagree", i) for i in found)
        else:
            records.append(_make(field_name, "agree", None))
    # A field flagged as never extracted has no entry in checked_fields.
    for field_name, found in by_field.items():
        if field_name not in checked_fields:
            records.extend(_make(field_name, "disagree", i) for i in found)
    return records
