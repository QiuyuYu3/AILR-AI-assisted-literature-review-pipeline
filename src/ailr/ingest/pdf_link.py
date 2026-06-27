"""Link Zotero-fetched PDFs to existing sources via a RIS export (no copy — record the path).

Matches each RIS record to a source already in the project (DOI first, fuzzy title fallback)
and records the absolute PDF path on that source. `preprocess` reads it directly.
"""

import os
import re
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
_TITLE_TIE_DELTA = 3  # titles within this score of the best are treated as ties, broken by year


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
    existing_by_doi = {
        s.doi.strip().lower(): s for s in existing if isinstance(s.doi, str) and s.doi.strip()
    }

    summary = PdfLinkSummary(total_records=len(records))

    for rec in records:
        attach = pdf_attachment_from_record(rec)
        if not attach:
            summary.no_attachment += 1
            continue

        src = _match_source(rec, existing_norms, existing_by_doi)
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

        store_path = _portable_path(pdf_path, project.root)
        if src.pdf_path is not None and Path(src.pdf_path) == store_path:
            summary.already_linked += 1
            continue

        project.db.update_pdf_path(src.id, store_path)
        summary.linked += 1

    return summary


def _portable_path(pdf_path: Path, project_root: Path) -> Path:
    """Store PDF paths relative to the project root so they resolve on any teammate's machine
    (the shared drive is mirrored). Falls back to absolute only across drives (Windows)."""
    try:
        return Path(os.path.relpath(pdf_path, project_root))
    except ValueError:
        return pdf_path


# Per-session cache: skip the RIS parse + match entirely when nothing under data/pdfs changed.
_auto_link_sig: dict[str, tuple] = {}


def _ris_signature(ris_files: list[Path]) -> tuple:
    out = []
    for r in ris_files:
        try:
            st = r.stat()
            out.append((str(r), st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((str(r), 0, 0))
    return tuple(out)


def auto_link_pdfs(project: Project, force: bool = False) -> PdfLinkSummary:
    """Idempotently link PDFs from any Zotero 'Export Files' RIS placed under data/pdfs.
    Triggered on entering the full-text pages; cached per session so it only re-parses when the
    Zotero export actually changes (the 'Re-scan' button passes force=True)."""
    pdfs_dir = project.root / "data" / "pdfs"
    agg = PdfLinkSummary()
    if not pdfs_dir.exists():
        return agg

    ris_files = sorted(pdfs_dir.rglob("*.ris"))
    sig = _ris_signature(ris_files)
    key = str(project.root)
    if not force and _auto_link_sig.get(key) == sig:
        return agg

    for ris in ris_files:
        try:
            s = link_pdfs_from_ris(project, ris)
        except Exception:
            continue
        agg.total_records += s.total_records
        agg.linked += s.linked
        agg.already_linked += s.already_linked
        agg.no_attachment += s.no_attachment
        agg.unmatched.extend(s.unmatched)
        agg.missing_files.extend(s.missing_files)

    _auto_link_sig[key] = sig
    return agg


def _match_source(
    rec: dict,
    existing_norms: list[tuple[str, Source]],
    existing_by_doi: dict[str, Source],
) -> Optional[Source]:
    doi = rec.get("doi")
    if isinstance(doi, str) and doi.strip():
        hit = existing_by_doi.get(doi.strip().lower())
        if hit is not None:
            return hit

    title = rec.get("title") or rec.get("primary_title")
    if not title:
        return None
    new_norm = normalize_title(title)
    scored = [(fuzz.token_set_ratio(new_norm, ex_norm), ex_src) for ex_norm, ex_src in existing_norms]
    if not scored:
        return None
    best_score = max(s for s, _ in scored)
    if best_score < _TITLE_THRESHOLD:
        return None
    # Break near-ties (e.g. "LAEO-Net" vs "LAEO-Net++", both ~100) by matching the record's year.
    top = [src for s, src in scored if s >= best_score - _TITLE_TIE_DELTA]
    if len(top) > 1:
        ry = _record_year(rec)
        if ry is not None:
            year_match = [src for src in top if src.year == ry]
            if len(year_match) == 1:
                return year_match[0]
    return max(scored, key=lambda t: t[0])[1]


def _record_year(rec: dict) -> Optional[int]:
    for key in ("year", "publication_year", "date"):
        v = rec.get(key)
        if v:
            m = re.search(r"\d{4}", str(v))
            if m:
                return int(m.group())
    return None
