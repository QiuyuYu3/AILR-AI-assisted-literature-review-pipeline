"""Machine-local path overrides, stored OUTSIDE the shared project so they never travel with it.

On shared drives (Box/OneDrive) each teammate's mount prefix differs, so a source's stored
pdf_path may not resolve on this machine. The user sets a local pdf_root (Settings tab); PDFs are
then looked up by filename anywhere under it. Kept in ~/.ailr/local.json, keyed by project root.
"""

import json
from pathlib import Path
from typing import Optional

_LOCAL_FILE = Path.home() / ".ailr" / "local.json"
_pdf_index_cache: dict = {}


def _load_local() -> dict:
    try:
        data = json.loads(_LOCAL_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def get_pdf_root(project_root: Path) -> Optional[Path]:
    """This machine's PDF base folder for the given project, if set."""
    entry = _load_local().get(str(project_root), {})
    root = entry.get("pdf_root") if isinstance(entry, dict) else None
    return Path(root) if root else None


def set_pdf_root(project_root: Path, path: Optional[str]) -> None:
    data = _load_local()
    key = str(project_root)
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


def resolve_pdf_path(src, project_root: Path) -> Optional[Path]:
    """Resolve a source's PDF: stored path first, then this machine's pdf_root override
    (match by filename anywhere under it). Returns None if nothing usable."""
    if not getattr(src, "pdf_path", None):
        return None
    p = Path(src.pdf_path)
    primary = p if p.is_absolute() else project_root / p
    if primary.exists():
        return primary
    root = get_pdf_root(project_root)
    if root and root.exists():
        direct = root / p.name
        if direct.exists():
            return direct
        hit = _pdf_index(root).get(p.name)
        if hit:
            return Path(hit)
    return primary if primary.exists() else None
