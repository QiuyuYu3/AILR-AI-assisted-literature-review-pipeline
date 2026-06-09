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
    pdf_path: Optional[Path] = None
    markdown_path: Optional[Path] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    imported_at: Optional[datetime] = None
