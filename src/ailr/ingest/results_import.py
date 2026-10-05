"""Import externally-run AI extraction results back into the project (the backend-hacking escape hatch).

Expected JSON: a list of records, each identifying a source (by "source_id" or "doi") and carrying an
"extraction" object {field_name: value | {"value": ..., "quote": ...}} and an optional "flag_check"
with a "decision" (include/exclude/uncertain) recorded as the AI's full-text screening verdict.
"""

from dataclasses import dataclass, field

from ailr.core.project import Project
from ailr.core.source import Source
from ailr.ingest.dedup import normalize_doi
from ailr.reviewers import ExtractionResult, ScreeningDecision


@dataclass
class ImportResultsSummary:
    total_records: int = 0
    imported: int = 0
    fields_written: int = 0
    flags_written: int = 0
    no_decision: list[int] = field(default_factory=list)  # imported without a flag_check decision
    unmatched: list[dict] = field(default_factory=list)
    mismatched: list[dict] = field(default_factory=list)  # source_id and DOI name different papers
    duplicates: list[int] = field(default_factory=list)  # papers named by more than one record
    errors: list[str] = field(default_factory=list)


@dataclass
class ImportScreeningSummary:
    total_records: int = 0
    imported: int = 0
    unmatched: list[dict] = field(default_factory=list)
    mismatched: list[dict] = field(default_factory=list)
    duplicates: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _resolve_source(project: Project, rec: dict) -> tuple[Source | None, bool]:
    """The paper a record names, and whether its source_id and DOI name different papers."""
    db, pid = project.db, project.project_id
    doi = normalize_doi(str(rec["doi"])) if rec.get("doi") else None
    by_doi = db.find_by_doi(pid, doi) if doi else None
    by_id = None
    if rec.get("source_id") is not None:
        try:
            by_id = db.get_source(int(rec["source_id"]))
        except (TypeError, ValueError):
            by_id = None
    if by_id is None:
        return by_doi, False
    if by_id.project_id != pid:
        return None, False
    if doi and ((by_doi is not None and by_doi.id != by_id.id)
                or (by_id.doi and normalize_doi(by_id.doi) != doi)):
        return None, True
    return by_id, False


def _pick_records(
    project: Project, records: list, summary: ImportResultsSummary | ImportScreeningSummary
) -> list[tuple[int, dict, Source]]:
    """One record per paper, the last that names it; everything else is reported on summary."""
    chosen: dict[int, tuple[int, dict, Source]] = {}
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            summary.errors.append(f"record {i}: not an object")
            continue
        src, mismatch = _resolve_source(project, rec)
        ref = {"source_id": rec.get("source_id"), "doi": rec.get("doi")}
        if mismatch:
            summary.mismatched.append(ref)
            continue
        if src is None:
            summary.unmatched.append(ref)
            continue
        if src.id in chosen and src.id not in summary.duplicates:
            summary.duplicates.append(src.id)
        chosen[src.id] = (i, rec, src)
    return list(chosen.values())


def import_ai_screening_results(
    project: Project, records: list[dict], *, stage: str = "abstract", extractor_id: str = "imported",
    llm_params: dict | None = None,
) -> ImportScreeningSummary:
    """Import externally-run AI SCREENING results: per record a decision + reasoning + confidence +
    matched_criteria + evidence_quotes, recorded as the AI reviewer's decision at `stage`."""
    db = project.db
    summary = ImportScreeningSummary(total_records=len(records))
    for i, rec, src in _pick_records(project, records, summary):
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
                llm_params=llm_params,
            )
        )
        summary.imported += 1
    return summary


def import_ai_results(project: Project, records: list[dict], *, extractor_id: str = "imported",
                      llm_params: dict | None = None) -> ImportResultsSummary:
    db = project.db
    summary = ImportResultsSummary(total_records=len(records))

    for i, rec, src in _pick_records(project, records, summary):
        extraction = rec.get("extraction") or {}
        if not isinstance(extraction, dict):
            summary.errors.append(f"record {i}: 'extraction' is not an object")
            continue

        fc = rec.get("flag_check") or {}
        decision = fc.get("decision") if isinstance(fc, dict) else None
        # One transaction per record: a failure part-way through leaves the previous import whole.
        with db._conn.transaction():
            # The AI extraction this replaces, in-app or imported, is retired to history, not deleted.
            previous = db.max_ai_extraction_id(src.id)
            if previous is not None:
                db.archive_ai_extractions_upto(src.id, previous)
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
                        llm_params=llm_params,
                    )
                )
                summary.fields_written += 1

            if decision in ("include", "exclude", "uncertain"):
                db.delete_screening_decision(src.id, extractor_id, stage="full_text", reviewer_type="ai")  # replace prior import
                db.insert_screening_decision(
                    ScreeningDecision(
                        decision=decision,
                        reasoning="(imported AI flag_check)",
                        reviewer_type="ai",
                        reviewer_id=extractor_id,
                        source_id=src.id,
                        stage="full_text",
                        llm_params=llm_params,
                    )
                )
                summary.flags_written += 1
            else:
                summary.no_decision.append(src.id)

        summary.imported += 1

    return summary
