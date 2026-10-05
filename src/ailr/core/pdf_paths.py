"""Resolve a source's stored PDF / markdown path. Both live under the shared project (data/pdfs,
data/markdown), so a path stored relative to the project root resolves on every teammate's machine;
absolute paths are used as-is for legacy/out-of-project files."""

import os
from pathlib import Path


def portable_path(path: Path, project_root: Path) -> Path:
    """Store paths relative to the project root so they resolve on any teammate's machine
    (the shared drive is mirrored). Falls back to absolute only across drives (Windows)."""
    try:
        return Path(os.path.relpath(path, project_root))
    except ValueError:
        return Path(path)


def path_for_db(path) -> str:
    """Forward slashes, so a path written on Windows reads back on macOS and Linux too."""
    return Path(path).as_posix()


def stored_path(value: str) -> Path:
    """A path from the database; macOS and Linux would read a Windows row's backslashes as part of a name."""
    return Path(value.replace("\\", "/"))


def resolve_pdf_path(pdf_path: str | None, project_root: Path) -> Path | None:
    if not pdf_path:
        return None
    p = stored_path(str(pdf_path))
    full = p if p.is_absolute() else project_root / p
    return full if full.exists() else None


def resolve_markdown_path(
    markdown_path: str | None,
    project_root: Path,
    source_id: int | None = None,
) -> Path | None:
    """Falls back to the canonical data/markdown/<id>.md, which is where preprocess always writes.
    Rows written before paths were stored relative hold an absolute path from whichever machine ran
    preprocess, so on a teammate's machine only the fallback resolves."""
    if markdown_path:
        p = stored_path(str(markdown_path))
        full = p if p.is_absolute() else project_root / p
        if full.exists():
            return full
    if source_id is not None:
        canonical = project_root / "data" / "markdown" / f"{source_id}.md"
        if canonical.exists():
            return canonical
    return None
