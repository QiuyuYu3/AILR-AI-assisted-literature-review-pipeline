"""Shared helpers for the Dash UI."""

import json
import os
from pathlib import Path
from typing import Optional

from ailr.core.project import Project

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


# --- Machine-local PDF base-folder override (for shared-drive setups where each
#     teammate's mount prefix differs, e.g. C:/Users/<name>/Box/...). Stored OUTSIDE
#     the shared lit_review.yaml so it never travels with the project. ---

_LOCAL_FILE = Path.home() / ".ailr" / "local.json"
_pdf_index_cache: dict = {}


def _load_local() -> dict:
    try:
        data = json.loads(_LOCAL_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def get_pdf_root() -> Optional[Path]:
    """This machine's PDF base folder for the current project, if set."""
    entry = _load_local().get(str(get_project().root), {})
    root = entry.get("pdf_root") if isinstance(entry, dict) else None
    return Path(root) if root else None


def set_pdf_root(path: Optional[str]) -> None:
    data = _load_local()
    key = str(get_project().root)
    entry = data.setdefault(key, {})
    if path and str(path).strip():
        entry["pdf_root"] = str(path).strip()
    else:
        entry.pop("pdf_root", None)
        if not entry:
            data.pop(key, None)
    try:
        _LOCAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LOCAL_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass
    _pdf_index_cache.clear()


def _pdf_index(root: Path) -> dict:
    """Lazy basename -> full path index of *.pdf under root (cached per session)."""
    key = str(root)
    if key not in _pdf_index_cache:
        idx: dict = {}
        try:
            for f in root.rglob("*.pdf"):
                idx.setdefault(f.name, str(f))
        except OSError:
            pass
        _pdf_index_cache[key] = idx
    return _pdf_index_cache[key]


def resolve_pdf_path(src) -> Optional[Path]:
    """Resolve a source's PDF: stored path first, then this machine's pdf_root override
    (match by filename anywhere under it). Returns None if nothing usable."""
    if not getattr(src, "pdf_path", None):
        return None
    p = Path(src.pdf_path)
    primary = p if p.is_absolute() else get_project().root / p
    if primary.exists():
        return primary
    root = get_pdf_root()
    if root and root.exists():
        direct = root / p.name
        if direct.exists():
            return direct
        hit = _pdf_index(root).get(p.name)
        if hit:
            return Path(hit)
    return primary if primary.exists() else None
