"""BibTeX parser. Uses `bibtexparser`; preserves unmapped fields in Source.metadata."""

import re
from pathlib import Path
from typing import Any

import bibtexparser
from bibtexparser.model import Entry

from ailr.core.source import Source
from ailr.exceptions import IngestError, InputNotFoundError

_KNOWN_KEYS = {
    "title", "abstract", "doi", "journal", "journaltitle", "booktitle",
    "author", "year", "date",
}


def parse_bibtex(file_path: Path, source_database: str | None = None) -> list[Source]:
    if not file_path.exists():
        raise InputNotFoundError(f"BibTeX file not found: {file_path}")

    try:
        text = file_path.read_text(encoding="utf-8-sig")
        # No parse stack: values are evaluated below, the same way for every entry
        library = bibtexparser.parse_string(text, parse_stack=[])
    except Exception as e:
        raise IngestError(f"Failed to parse BibTeX file {file_path}: {e}") from e

    entries = list(library.entries)
    broken = []
    for block in library.failed_blocks:
        # A repeated citation key (or field) still holds a paper of its own
        if isinstance(block.ignore_error_block, Entry):
            entries.append(block.ignore_error_block)
        else:
            broken.append(block)
    if broken:
        where = "; ".join(f"line {(b.start_line or 0) + 1}: {(b.raw or '').strip().splitlines()[0][:60]}" for b in broken[:5])
        raise IngestError(f"{len(broken)} BibTeX entr{'y' if len(broken) == 1 else 'ies'} in {file_path.name} could not be read ({where}).")

    strings = {s.key.lower(): s.value for s in library.strings}
    entries.sort(key=lambda e: e.start_line or 0)
    return [_entry_to_source(_fields(e, strings), source_database) for e in entries]


def _fields(entry: Entry, strings: dict[str, str]) -> dict[str, str]:
    # Field names are case-insensitive in BibTeX; a later repeat of a name wins
    return {f.key.lower(): _evaluate(f.value, strings) for f in entry.fields}


def _evaluate(raw: Any, strings: dict[str, str], depth: int = 0) -> str:
    if not isinstance(raw, str):
        return "" if raw is None else str(raw)
    out = []
    for part in _split_concatenation(raw):
        if len(part) >= 2 and (part[0], part[-1]) in (("{", "}"), ('"', '"')):
            out.append(part[1:-1])
        elif part.lower() in strings and depth < 10:
            out.append(_evaluate(strings[part.lower()], strings, depth + 1))
        else:
            out.append(part)  # a number, or a macro the file never defines
    return "".join(out)


def _split_concatenation(raw: str) -> list[str]:
    parts, current, depth, quoted = [], [], 0, False
    for ch in raw:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == '"' and depth == 0:
            quoted = not quoted
        elif ch == "#" and depth == 0 and not quoted:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    parts.append("".join(current).strip())
    return [p for p in parts if p]


def _entry_to_source(entry: dict[str, str], source_database: str | None) -> Source:
    title = _strip_braces(entry.get("title", "")).strip()
    abstract_raw = _strip_braces(entry.get("abstract", "")).strip()
    abstract = abstract_raw or None
    doi = (entry.get("doi") or "").strip() or None
    journal = _strip_braces(entry.get("journal") or entry.get("journaltitle") or entry.get("booktitle") or "").strip() or None

    authors = _parse_authors(entry.get("author", ""))
    year = _coerce_year(entry.get("year") or entry.get("date"))

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
    parts = [p.strip() for p in re.split(r"\s+and\s+", raw)]
    return [_strip_braces(p) for p in parts if p.strip()]


def _coerce_year(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    digits = "".join(c for c in str(raw) if c.isdigit())[:4]
    if len(digits) == 4:
        try:
            return int(digits)
        except ValueError:
            return None
    return None
