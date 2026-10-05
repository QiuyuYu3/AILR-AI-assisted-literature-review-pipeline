"""Link Zotero-fetched PDFs to existing sources via a RIS export (no copy — record the path).

Matches each RIS record to a source already in the project (DOI first, fuzzy title fallback)
and records the absolute PDF path on that source. `preprocess` reads it directly.
"""

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import rispy

from ailr.core.pdf_paths import portable_path
from ailr.core.project import Project
from ailr.core.source import Source
from ailr.exceptions import InputNotFoundError
from ailr.ingest.dedup import TITLE_MATCH_SCORER, normalize_doi, normalize_title
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
    existing_by_doi = {normalize_doi(s.doi): s for s in existing if isinstance(s.doi, str) and normalize_doi(s.doi)}

    summary = PdfLinkSummary(total_records=len(records))

    for rec in records:
        attach = pdf_attachment_from_record(rec)
        if not attach:
            summary.no_attachment += 1
            continue

        src = _match_source(rec, existing_norms, existing_by_doi)
        if src is None:
            entry = {"title": (rec.get("title") or rec.get("primary_title") or "")[:80], "doi": rec.get("doi")}
            if _record_doi(rec):
                # Say so when only the DOI kept a title match out, so the user can link it by hand.
                blocked = _match_source({**rec, "doi": None}, existing_norms, {})
                if blocked is not None:
                    entry["doi_differs_from"] = blocked.id
            summary.unmatched.append(entry)
            continue

        pdf_path = Path(attach)
        if not pdf_path.is_absolute():
            pdf_path = (base / pdf_path).resolve()
        if not pdf_path.exists():
            summary.missing_files.append({"source_id": src.id, "path": str(pdf_path)})
            continue

        store_path = portable_path(pdf_path, project.root)
        if src.pdf_path is not None and Path(src.pdf_path) == store_path:
            summary.already_linked += 1
            continue

        project.db.update_pdf_path(src.id, store_path)
        summary.linked += 1

    return summary


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


_auto_link_lock = threading.Lock()
_auto_link_running: set[str] = set()


def auto_link_pdfs_on_entry(project: Project) -> None:
    """Entry hook for the full-text pages: link synchronously the first time in a session, in the
    background every time after.

    Deciding whether anything changed means walking data/pdfs for RIS files, and that walk is what
    every entry pays — the signature cache only saves the parse behind it. On a synced drive
    (Box/Dropbox) the walk alone can take seconds, and it used to block the tab switch. The first
    entry stays synchronous so the list is right on its first paint; later ones render immediately
    and pick up any newly linked PDF on the next refresh.
    """
    key = str(project.root)
    if key not in _auto_link_sig:
        try:
            auto_link_pdfs(project)
        except Exception:
            pass
        return

    with _auto_link_lock:
        if key in _auto_link_running:
            return
        _auto_link_running.add(key)

    def _run() -> None:
        try:
            auto_link_pdfs(project)
        except Exception:
            pass
        finally:
            with _auto_link_lock:
                _auto_link_running.discard(key)

    threading.Thread(target=_run, daemon=True).start()


def _match_source(
    rec: dict,
    existing_norms: list[tuple[str, Source]],
    existing_by_doi: dict[str, Source],
) -> Source | None:
    doi = _record_doi(rec)
    if doi:
        hit = existing_by_doi.get(doi)
        if hit is not None:
            return hit

    title = rec.get("title") or rec.get("primary_title")
    if not title:
        return None
    new_norm = normalize_title(title)
    # A paper with a different DOI is a different paper (or another version of it), however close the title.
    scored = [(TITLE_MATCH_SCORER(new_norm, ex_norm), ex_src) for ex_norm, ex_src in existing_norms
              if not (doi and normalize_doi(ex_src.doi) and normalize_doi(ex_src.doi) != doi)]
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


def _record_doi(rec: dict) -> str | None:
    doi = rec.get("doi")
    return normalize_doi(doi) if isinstance(doi, str) else None


def _record_year(rec: dict) -> int | None:
    for key in ("year", "publication_year", "date"):
        v = rec.get(key)
        if v:
            m = re.search(r"\d{4}", str(v))
            if m:
                return int(m.group())
    return None
