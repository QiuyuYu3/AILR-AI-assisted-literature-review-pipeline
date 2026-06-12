"""Resolve a source's stored PDF path. PDFs live under the shared project (data/pdfs), so a path
stored relative to the project root resolves on every teammate's machine; absolute paths are used
as-is for legacy/out-of-project files."""

from pathlib import Path
from typing import Optional


def resolve_pdf_path(pdf_path: Optional[str], project_root: Path) -> Optional[Path]:
    if not pdf_path:
        return None
    p = Path(pdf_path)
    full = p if p.is_absolute() else project_root / p
    return full if full.exists() else None
