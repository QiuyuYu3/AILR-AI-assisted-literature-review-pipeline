"""Resolve the prompt version a run is stamped with, snapshotting the prompt when it has changed.

Outside `ui/` on purpose: the CLI stamps the same versions. While these lived as UI-private
helpers, `ailr screen` labelled every decision "v1" whatever the prompt actually said, and two
different prompts could end up sharing that label in one database.
"""

from pathlib import Path
from typing import Any

from ailr.criteria import resolve_criteria
from ailr.extraction import (
    compose_extraction_prompt,
    compose_schema,
    compose_screening_prompt,
    schema_to_markdown,
)


def _read(root: Path, rel: str) -> str:
    try:
        return (root / rel).read_text(encoding="utf-8")
    except OSError:
        return ""


def criteria_text(project: Any) -> str:
    """The criteria injected as {{criteria}}: structured criteria.yaml, or the legacy free-text file."""
    text, _ = resolve_criteria(project.root, project.config.screening)
    return text if text.strip() else ""


def screening_composed(project: Any) -> str:
    """The fully-resolved screening prompt a fresh run would use right now."""
    cfg = project.config.screening
    return compose_screening_prompt(
        _read(project.root, cfg.prompt),
        criteria=criteria_text(project),
        additional=_read(project.root, cfg.additional),
    )


def extraction_composed(project: Any) -> str:
    """The fully-resolved extraction prompt a fresh run would use right now (criteria + schema +
    additional filled in). Also used to detect stale extractions without cutting a new version."""
    cfg = project.config.extraction
    try:
        schema_md = schema_to_markdown(compose_schema(project.root / cfg.schema_path))
    except Exception:
        schema_md = ""
    return compose_extraction_prompt(
        _read(project.root, cfg.prompt),
        criteria=criteria_text(project),
        schema_md=schema_md,
        additional=_read(project.root, cfg.additional),
    )


def _resolve(project: Any, prompt_type: str, template: str, composed: str) -> str:
    db = project.db
    pid = project.project_id
    latest = db.latest_prompt_version(pid, prompt_type)
    if latest is not None:
        prev = db.get_prompt_version(pid, prompt_type, latest)
        # Bump on any change to the resolved prompt, not just the editable template.
        if prev and prev.get("composed") == composed:
            return latest
    if not composed.strip() and latest is None:
        return "unversioned"
    return db.save_prompt_version(pid, prompt_type, template, None, composed=composed)


def screening_prompt_version(project: Any) -> str:
    return _resolve(
        project,
        "screening",
        _read(project.root, project.config.screening.prompt),
        screening_composed(project),
    )


def extraction_prompt_version(project: Any) -> str:
    return _resolve(
        project,
        "extraction",
        _read(project.root, project.config.extraction.prompt),
        extraction_composed(project),
    )
