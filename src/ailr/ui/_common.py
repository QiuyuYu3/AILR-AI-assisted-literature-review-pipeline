"""Shared helpers for the Dash UI."""

import json
import os
from pathlib import Path
from typing import Optional

from ailr.core import pdf_paths
from ailr.core.project import Project
from ailr.extraction import compose_prompt  # noqa: F401  (re-exported for UI callers)

_project: Optional[Project] = None


def format_authors(raw: object, limit: int = 3) -> str:
    """Render a sources.authors value (JSON list or list) as 'A; B; C et al.'."""
    if not raw:
        return ""
    authors = raw
    if isinstance(raw, str):
        try:
            authors = json.loads(raw)
        except (ValueError, TypeError):
            return raw
    if not isinstance(authors, list):
        return str(authors)
    shown = "; ".join(str(a) for a in authors[:limit])
    return f"{shown} et al." if len(authors) > limit else shown


_RECENT_FILE = Path.home() / ".ailr" / "recent.json"


def list_recent_projects() -> list[str]:
    """Recently-opened project folders that still have a lit_review.yaml, most-recent first."""
    try:
        data = json.loads(_RECENT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [p for p in data if isinstance(p, str) and (Path(p) / "lit_review.yaml").exists()]


def add_recent_project(path: Path) -> None:
    p = str(Path(path).resolve())
    try:
        recent = json.loads(_RECENT_FILE.read_text(encoding="utf-8"))
        if not isinstance(recent, list):
            recent = []
    except (OSError, ValueError):
        recent = []
    recent = [p] + [x for x in recent if x != p]
    try:
        _RECENT_FILE.parent.mkdir(parents=True, exist_ok=True)
        _RECENT_FILE.write_text(json.dumps(recent[:15], indent=2), encoding="utf-8")
    except OSError:
        pass


def remove_recent_project(path: Path) -> None:
    p = str(Path(path).resolve())
    try:
        recent = json.loads(_RECENT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    try:
        _RECENT_FILE.write_text(json.dumps([x for x in recent if x != p], indent=2), encoding="utf-8")
    except OSError:
        pass


def clear_current_project_data() -> dict:
    """Wipe all data of the active project but keep the project + its config/prompt files."""
    proj = get_project()
    return proj.db.delete_project_data(proj.project_id, keep_project_row=True)


def delete_current_project() -> dict:
    """Delete the active project entirely (data + project row + recent entry), keeping files.
    Clears the active project and AILR_PROJECT so the app falls back to the project manager."""
    global _project
    proj = get_project()
    root = proj.root
    counts = proj.db.delete_project_data(proj.project_id, keep_project_row=False)
    proj.db.close()
    remove_recent_project(root)
    _project = None
    os.environ.pop("AILR_PROJECT", None)
    return counts


def has_project() -> bool:
    """Whether a project is loaded (or named via AILR_PROJECT) — gates the project manager."""
    return _project is not None or bool(os.environ.get("AILR_PROJECT"))


def get_project() -> Project:
    """Load the project named by the AILR_PROJECT env var (cached for the app lifetime)."""
    global _project
    if _project is None:
        path = os.environ.get("AILR_PROJECT")
        if not path:
            raise RuntimeError("AILR_PROJECT environment variable is not set.")
        _project = Project.load(Path(path))
        add_recent_project(_project.root)
    return _project


def reload_project() -> Project:
    """Re-read lit_review.yaml after a workflow toggle / external edit."""
    global _project
    if _project is None:
        return get_project()
    _project = Project.load(_project.root)
    return _project


def switch_project(path: Path) -> Project:
    """Point the running app at a different project folder (UI project switcher)."""
    global _project
    _project = Project.load(Path(path))
    add_recent_project(_project.root)
    return _project


def create_project(
    parent: Path,
    name: str,
    mode: str = "assisted",
    database_url: Optional[str] = None,
) -> Project:
    """Scaffold a new project under parent/name and switch the app to it.

    When database_url is set (e.g. a PostgreSQL URL), it is written into the project's
    lit_review.yaml so the project uses that shared DB instead of the local SQLite file.
    """
    global _project
    root = Path(parent).resolve() / name
    Project.init(root, mode=mode)
    if database_url and database_url.strip():
        import yaml

        cfg_path = root / "lit_review.yaml"
        try:
            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            data = {}
        data.setdefault("storage", {})["database_url"] = database_url.strip()
        cfg_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    _project = Project.load(root)
    add_recent_project(root)
    return _project


def resolve_pdf_path(src) -> Optional[Path]:
    """Resolve a source's stored PDF path (absolute, or relative to the project root)."""
    return pdf_paths.resolve_pdf_path(getattr(src, "pdf_path", None), get_project().root)


def read_text_or(path: Path, fallback: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return fallback


def read_criteria(fallback: str = "(criteria file not found)") -> str:
    """Read the project's inclusion/exclusion criteria file."""
    project = get_project()
    return read_text_or(project.root / project.config.screening.criteria, fallback)


def read_screening_prompt(fallback: str = "(screening prompt file not found)") -> str:
    project = get_project()
    return read_text_or(project.root / project.config.screening.prompt, fallback)


def _short_author_year(src) -> str:
    if not src.authors:
        return f"({src.year})" if src.year else ""
    first = src.authors[0].split(",")[0].strip()
    return f"{first} {src.year}" if src.year else first
