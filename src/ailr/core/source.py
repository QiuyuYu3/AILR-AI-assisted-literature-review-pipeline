"""Source: an in-memory bibliographic record."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


@dataclass
class Source:
    title: str
    id: Optional[int] = None
    project_id: Optional[int] = None
    doi: Optional[str] = None
    pmid: Optional[str] = None
    abstract: Optional[str] = None
    authors: list[str] = field(default_factory=list)
    year: Optional[int] = None
    journal: Optional[str] = None
    source_database: Optional[str] = None
    # PRISMA 2020 identification arm: 'database' (databases and registers) or 'other'
    # (citation searching, hand searching, websites, organisations).
    identification_route: str = "database"
    pdf_path: Optional[Path] = None
    markdown_path: Optional[Path] = None
    # PRISMA's "reports not retrieved": the full text was sought and could not be obtained.
    # Distinct from simply having no markdown yet, which only means not done.
    full_text_not_retrieved: bool = False
    # Set when this report is a companion of another (same study, several publications): the id
    # of the report representing the study. NULL/None means this report is its own study.
    study_group_id: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    imported_at: Optional[datetime] = None


_RECORD_FIELDS = ("title", "doi", "pmid", "abstract", "authors", "year", "journal",
                  "source_database", "metadata")


def source_to_record(src: "Source") -> dict[str, Any]:
    """JSON-safe dict of a source's bibliographic fields (for stashing/restoring; no id/paths)."""
    return {f: getattr(src, f) for f in _RECORD_FIELDS}


def source_from_record(data: dict[str, Any]) -> "Source":
    """Rebuild a Source from source_to_record output."""
    return Source(**{f: data.get(f) for f in _RECORD_FIELDS if data.get(f) is not None})
