"""Append-only audit log: every AI decision and human override goes here as JSONL."""

from pathlib import Path
from typing import Any


def log_event(
    audit_log_path: Path,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Append a single JSON line to the audit log. Creates parent dir and file if missing."""
    raise NotImplementedError


def read_events(
    audit_log_path: Path,
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    """Read all events; optionally filter by event_type."""
    raise NotImplementedError
