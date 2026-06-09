"""BibTeX parser. Uses `bibtexparser`; preserves unmapped fields in Source.metadata."""

from pathlib import Path
from typing import Any, Optional

import bibtexparser

from ailr.core.source import Source
from ailr.exceptions import IngestError, InputNotFoundError

_KNOWN_KEYS = {
    "title", "abstract", "doi", "journal", "booktitle",
    "author", "year", "ENTRYTYPE", "ID",
}


def parse_bibtex(file_path: Path, source_database: Optional[str] = None) -> list[Source]:
    if not file_path.exists():
        raise InputNotFoundError(f"BibTeX file not found: {file_path}")

    try:
        with open(file_path, encoding="utf-8-sig") as f:
            db = bibtexparser.load(f)
    except Exception as e:
        raise IngestError(f"Failed to parse BibTeX file {file_path}: {e}") from e

    return [_entry_to_source(entry, source_database) for entry in db.entries]


def _entry_to_source(entry: dict[str, Any], source_database: Optional[str]) -> Source:
    title = _strip_braces(entry.get("title", "")).strip()
    abstract_raw = _strip_braces(entry.get("abstract", "")).strip()
    abstract = abstract_raw or None
    doi = (entry.get("doi") or "").strip() or None
    journal = _strip_braces(entry.get("journal") or entry.get("booktitle") or "").strip() or None

    authors = _parse_authors(entry.get("author", ""))
    year = _coerce_year(entry.get("year"))

    metadata = {k: v for k, v in entry.items() if k not in _KNOWN_KEYS}

    return Source(
        title=title,
        abstract=abstract,
        doi=doi,
        authors=authors,
        year=year,
        journal=journal,
        source_database=source_database,
        metadata=metadata,
    )


def _strip_braces(text: Any) -> str:
    if not isinstance(text, str):
        return str(text) if text is not None else ""
    return text.replace("{", "").replace("}", "")


def _parse_authors(raw: str) -> list[str]:
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(" and ")]
    return [_strip_braces(p) for p in parts if p.strip()]


def _coerce_year(raw: Any) -> Optional[int]:
    if raw is None or raw == "":
        return None
    digits = "".join(c for c in str(raw) if c.isdigit())[:4]
    if len(digits) == 4:
        try:
            return int(digits)
        except ValueError:
            return None
    return None
