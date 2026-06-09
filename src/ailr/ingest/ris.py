"""RIS parser with WoS-aware field handling.

Uses `rispy` for tag parsing. rispy's TAG_KEY_MAPPING translates RIS tags to
descriptive Python keys (TI -> title, T2 -> secondary_title, AU -> authors, etc.).
Any rispy key not consumed by the mapping below is preserved in Source.metadata.
"""

from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote

import rispy

from ailr.core.source import Source
from ailr.exceptions import IngestError, InputNotFoundError

KNOWN_KEYS = {
    "title",
    "primary_title",
    "abstract",
    "doi",
    "secondary_title",
    "journal_name",
    "authors",
    "first_authors",
    "year",
    "publication_year",
    "type_of_reference",
}


def parse_ris(file_path: Path, source_database: Optional[str] = None) -> list[Source]:
    if not file_path.exists():
        raise InputNotFoundError(f"RIS file not found: {file_path}")

    try:
        with open(file_path, encoding="utf-8-sig") as f:
            records = rispy.load(f)
    except Exception as e:
        raise IngestError(f"Failed to parse RIS file {file_path}: {e}") from e

    if source_database is None:
        source_database = detect_source_database(records)

    return [_record_to_source(rec, source_database) for rec in records]


def detect_source_database(records: list[dict[str, Any]]) -> Optional[str]:
    if not records:
        return None
    first = records[0]
    accession = first.get("accession_number")
    if isinstance(accession, str) and accession.startswith("WOS:"):
        return "WoS"
    if isinstance(accession, list):
        for a in accession:
            if isinstance(a, str) and a.startswith("WOS:"):
                return "WoS"
    if first.get("name_of_database", "").upper() == "PUBMED" or first.get("pubmed_id"):
        return "PubMed"
    return None


def _record_to_source(rec: dict[str, Any], source_database: Optional[str]) -> Source:
    title = (rec.get("title") or rec.get("primary_title") or "").strip()
    abstract = rec.get("abstract")
    doi = rec.get("doi")
    journal = rec.get("secondary_title") or rec.get("journal_name")
    authors = rec.get("authors") or rec.get("first_authors") or []
    if isinstance(authors, str):
        authors = [authors]

    year_raw = rec.get("year") or rec.get("publication_year")
    year = _coerce_year(year_raw)

    pmid_raw = rec.get("pubmed_id") or rec.get("accession_number")
    pmid = _extract_pmid(pmid_raw)

    metadata = {k: v for k, v in rec.items() if k not in KNOWN_KEYS}

    return Source(
        title=title,
        abstract=abstract.strip() if isinstance(abstract, str) else abstract,
        doi=doi.strip() if isinstance(doi, str) else doi,
        pmid=pmid,
        authors=list(authors),
        year=year,
        journal=journal.strip() if isinstance(journal, str) else journal,
        source_database=source_database,
        metadata=metadata,
    )


def _coerce_year(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        digits = "".join(c for c in raw if c.isdigit())[:4]
        if len(digits) == 4:
            try:
                return int(digits)
            except ValueError:
                return None
    return None


def _extract_pmid(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if isinstance(raw, str) and raw.isdigit():
        return raw
    return None


def pdf_attachment_from_record(rec: dict[str, Any]) -> Optional[str]:
    """First PDF path from a parsed RIS record's L1/L2 attachment tags (Zotero "Export Files")."""
    for key in ("file_attachments1", "file_attachments2"):
        val = rec.get(key)
        if isinstance(val, str) and val.strip().lower().endswith(".pdf"):
            return _clean_attachment_path(val.strip())
    return None


def _clean_attachment_path(p: str) -> str:
    if p.lower().startswith("file://"):
        p = p[len("file://"):]
    if "%" in p:  # only decode genuinely percent-encoded paths
        p = unquote(p)
    return p
