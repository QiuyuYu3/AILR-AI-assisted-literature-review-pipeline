"""Link Zotero-fetched PDFs to existing sources via a RIS export (no copy — record the path).

Matches each RIS record to a source already in the project (DOI first, fuzzy title fallback)
and records the absolute PDF path on that source. `preprocess` reads it directly.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import rispy
from rapidfuzz import fuzz

from ailr.core.project import Project
from ailr.core.source import Source
from ailr.exceptions import InputNotFoundError
from ailr.ingest.dedup import normalize_title
from ailr.ingest.ris import pdf_attachment_from_record

_TITLE_THRESHOLD = 90


@dataclass
class PdfLinkSummary:
    total_records: int = 0
    linked: int = 0           # newly linked this run
    already_linked: int = 0   # source already pointed at this same PDF
    no_attachment: int = 0
    unmatched: list[dict] = field(default_factory=list)      # record had a PDF but no source matched
    missing_files: list[dict] = field(default_factory=list)  # source matched but the PDF file is gone


def link_pdfs_from_ris(project: Project, ris_path: Path) -> PdfLinkSummary:
    ris_path = Path(ris_path).expanduser().resolve()
    if not ris_path.exists():
        raise InputNotFoundError(f"RIS file not found: {ris_path}")

    with open(ris_path, encoding="utf-8-sig") as f:
        records = rispy.load(f)

    base = ris_path.parent
    existing = project.db.list_sources(project.project_id)
    existing_norms = [(normalize_title(s.title), s) for s in existing]

    summary = PdfLinkSummary(total_records=len(records))

    for rec in records:
        attach = pdf_attachment_from_record(rec)
        if not attach:
            summary.no_attachment += 1
            continue

        src = _match_source(project, rec, existing_norms)
        if src is None:
            summary.unmatched.append(
                {"title": (rec.get("title") or rec.get("primary_title") or "")[:80], "doi": rec.get("doi")}
            )
            continue

        pdf_path = Path(attach)
        if not pdf_path.is_absolute():
            pdf_path = (base / pdf_path).resolve()
        if not pdf_path.exists():
            summary.missing_files.append({"source_id": src.id, "path": str(pdf_path)})
            continue

        if src.pdf_path is not None and Path(src.pdf_path) == pdf_path:
            summary.already_linked += 1
            continue

        project.db.update_pdf_path(src.id, pdf_path)
        summary.linked += 1

    return summary


def _match_source(
    project: Project,
    rec: dict,
    existing_norms: list[tuple[str, Source]],
) -> Optional[Source]:
    doi = rec.get("doi")
    if isinstance(doi, str) and doi.strip():
        hit = project.db.find_by_doi(project.project_id, doi.strip())
        if hit is not None:
            return hit

    title = rec.get("title") or rec.get("primary_title")
    if not title:
        return None
    new_norm = normalize_title(title)
    best_score, best = 0, None
    for ex_norm, ex_src in existing_norms:
        score = fuzz.token_set_ratio(new_norm, ex_norm)
        if score > best_score:
            best_score, best = score, ex_src
    return best if best_score >= _TITLE_THRESHOLD else None
