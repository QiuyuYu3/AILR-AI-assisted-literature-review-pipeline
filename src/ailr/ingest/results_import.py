"""Import externally-run AI extraction results back into the project (the backend-hacking escape hatch).

Expected JSON: a list of records, each identifying a source (by "source_id" or "doi") and carrying an
"extraction" object {field_name: value | {"value": ..., "quote": ...}} and an optional "flag_check"
with a "decision" (include/exclude/uncertain) recorded as the AI's full-text screening verdict.
"""

from dataclasses import dataclass, field
from typing import Any

from ailr.core.project import Project
from ailr.reviewers import ExtractionResult, ScreeningDecision


@dataclass
class ImportResultsSummary:
    total_records: int = 0
    imported: int = 0
    fields_written: int = 0
    flags_written: int = 0
    unmatched: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class ImportScreeningSummary:
    total_records: int = 0
    imported: int = 0
    unmatched: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _resolve_source(project: Project, rec: dict):
    db, pid = project.db, project.project_id
    src = None
    if rec.get("source_id") is not None:
        try:
            src = db.get_source(int(rec["source_id"]))
        except (TypeError, ValueError):
            src = None
    if src is None and rec.get("doi"):
        src = db.find_by_doi(pid, str(rec["doi"]).strip())
    return src if (src is not None and src.project_id == pid) else None


def import_ai_screening_results(
    project: Project, records: list[dict], *, stage: str = "abstract", extractor_id: str = "imported"
) -> ImportScreeningSummary:
    """Import externally-run AI SCREENING results: per record a decision + reasoning + confidence +
    matched_criteria + evidence_quotes, recorded as the AI reviewer's decision at `stage`."""
    db = project.db
    summary = ImportScreeningSummary(total_records=len(records))
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            summary.errors.append(f"record {i}: not an object")
            continue
        src = _resolve_source(project, rec)
        if src is None:
            summary.unmatched.append({"source_id": rec.get("source_id"), "doi": rec.get("doi")})
            continue
        decision = rec.get("decision")
        if decision not in ("include", "exclude", "uncertain"):
            summary.errors.append(f"record {i}: decision must be include/exclude/uncertain (got {decision!r})")
            continue
        db.delete_screening_decision(src.id, extractor_id, stage=stage)  # replace prior import
        db.insert_screening_decision(
            ScreeningDecision(
                decision=decision,
                reasoning=rec.get("reasoning"),
                confidence=rec.get("confidence"),
                matched_criteria=rec.get("matched_criteria"),
                evidence_quotes=rec.get("evidence_quotes"),
                reviewer_type="ai",
                reviewer_id=extractor_id,
                source_id=src.id,
                stage=stage,
            )
        )
        summary.imported += 1
    return summary


def import_ai_results(project: Project, records: list[dict], *, extractor_id: str = "imported") -> ImportResultsSummary:
    db = project.db
    pid = project.project_id
    summary = ImportResultsSummary(total_records=len(records))

    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            summary.errors.append(f"record {i}: not an object")
            continue

        src = None
        if rec.get("source_id") is not None:
            try:
                src = db.get_source(int(rec["source_id"]))
            except (TypeError, ValueError):
                src = None
        if src is None and rec.get("doi"):
            src = db.find_by_doi(pid, str(rec["doi"]).strip())
        if src is None or src.project_id != pid:
            summary.unmatched.append({"source_id": rec.get("source_id"), "doi": rec.get("doi")})
            continue

        extraction = rec.get("extraction") or {}
        if not isinstance(extraction, dict):
            summary.errors.append(f"record {i}: 'extraction' is not an object")
            continue

        db.delete_extractions(src.id, "ai")  # replace any prior AI extraction for this source
        for field_name, payload in extraction.items():
            if isinstance(payload, dict) and "value" in payload:
                value, quote = payload.get("value"), payload.get("quote")
            else:
                value, quote = payload, None
            db.insert_extraction(
                ExtractionResult(
                    extractor_type="ai",
                    extractor_id=extractor_id,
                    field_name=field_name,
                    value=value,
                    source_quote=quote,
                    source_id=src.id,
                )
            )
            summary.fields_written += 1

        fc = rec.get("flag_check") or {}
        decision = fc.get("decision") if isinstance(fc, dict) else None
        if decision in ("include", "exclude", "uncertain"):
            db.insert_screening_decision(
                ScreeningDecision(
                    decision=decision,
                    reasoning="(imported AI flag_check)",
                    reviewer_type="ai",
                    reviewer_id=extractor_id,
                    source_id=src.id,
                    stage="full_text",
                )
            )
            summary.flags_written += 1

        summary.imported += 1

    return summary
