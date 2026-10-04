"""RIS parser with WoS-aware field handling.

Uses `rispy` for tag parsing. rispy's TAG_KEY_MAPPING translates RIS tags to
descriptive Python keys (TI -> title, T2 -> secondary_title, AU -> authors, etc.).
Any rispy key not consumed by the mapping below is preserved in Source.metadata.
"""

import re
from pathlib import Path
from typing import Any
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


def parse_ris(file_path: Path, source_database: str | None = None) -> list[Source]:
    if not file_path.exists():
        raise InputNotFoundError(f"RIS file not found: {file_path}")

    try:
        text = _close_last_record(file_path.read_text(encoding="utf-8-sig"))
        records = rispy.loads(text)
    except Exception as e:
        raise IngestError(f"Failed to parse RIS file {file_path}: {e}") from e

    if source_database is None:
        source_database = detect_source_database(records)

    return [_record_to_source(rec, source_database) for rec in records]


def _close_last_record(text: str) -> str:
    """rispy silently drops a final record without its ER line, which a truncated export lacks."""
    starts = [m.start() for m in re.finditer(r"^TY  -", text, re.MULTILINE)]
    if starts and not re.search(r"^ER  -", text[starts[-1]:], re.MULTILINE):
        return text.rstrip() + "\nER  - \n"
    return text


def detect_source_database(records: list[dict[str, Any]]) -> str | None:
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


def _record_to_source(rec: dict[str, Any], source_database: str | None) -> Source:
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


def _coerce_year(raw: Any) -> int | None:
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


def _extract_pmid(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str) and raw.isdigit():
        return raw
    return None


def pdf_attachment_from_record(rec: dict[str, Any]) -> str | None:
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
