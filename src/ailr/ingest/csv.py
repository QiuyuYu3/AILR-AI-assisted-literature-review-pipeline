"""CSV / TSV parser. Case-insensitive column name detection for common WoS/Scopus/PubMed exports."""

import csv as _csv
from pathlib import Path
from typing import Any, Optional

from ailr.core.source import Source
from ailr.exceptions import IngestError, InputNotFoundError

_COLUMN_ALIASES: dict[str, set[str]] = {
    "title": {"title", "ti", "article title", "primary_title"},
    "abstract": {"abstract", "ab", "abstract note"},
    "doi": {"doi", "do", "digital object identifier"},
    "year": {"year", "py", "publication year", "publication_year"},
    "authors": {"authors", "author", "au"},
    "journal": {"journal", "source", "so", "t2", "secondary title", "journal name", "publication title"},
    "pmid": {"pmid", "pubmed id", "pubmed_id"},
}


def parse_csv(
    file_path: Path,
    source_database: Optional[str] = None,
    delimiter: Optional[str] = None,
) -> list[Source]:
    if not file_path.exists():
        raise InputNotFoundError(f"File not found: {file_path}")

    if delimiter is None:
        ext = file_path.suffix.lower()
        delimiter = "\t" if ext in (".tsv", ".txt") else ","

    try:
        with open(file_path, encoding="utf-8-sig", newline="") as f:
            reader = _csv.DictReader(f, delimiter=delimiter)
            rows = list(reader)
    except Exception as e:
        raise IngestError(f"Failed to parse CSV/TSV {file_path}: {e}") from e

    if not rows:
        return []

    headers = list(rows[0].keys())
    lookup = _build_lookup(headers)
    return [_row_to_source(r, lookup, headers, source_database) for r in rows]


def _build_lookup(headers: list[str]) -> dict[str, str]:
    """canonical name -> actual header column name (case-insensitive alias match)."""
    out: dict[str, str] = {}
    lowered = {h.lower().strip(): h for h in headers if h}
    for canonical, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lowered:
                out[canonical] = lowered[alias]
                break
    return out


def _row_to_source(
    row: dict[str, Any],
    lookup: dict[str, str],
    headers: list[str],
    source_database: Optional[str],
) -> Source:
    def _get(canonical: str) -> Optional[str]:
        col = lookup.get(canonical)
        if col is None:
            return None
        v = row.get(col)
        if v is None:
            return None
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return str(v)

    title = _get("title") or ""
    authors = _split_authors(_get("authors") or "")
    year = _coerce_year(_get("year"))

    matched_cols = set(lookup.values())
    metadata = {h: row[h] for h in headers if h and h not in matched_cols and row.get(h)}

    return Source(
        title=title,
        abstract=_get("abstract"),
        doi=_get("doi"),
        pmid=_get("pmid"),
        authors=authors,
        year=year,
        journal=_get("journal"),
        source_database=source_database,
        metadata=metadata,
    )


def _split_authors(raw: str) -> list[str]:
    if not raw:
        return []
    for sep in ["; ", " and ", "\n"]:
        if sep in raw:
            return [p.strip() for p in raw.split(sep) if p.strip()]
    if raw.count(",") > 1:
        return [p.strip() for p in raw.split(",") if p.strip()]
    s = raw.strip()
    return [s] if s else []


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
