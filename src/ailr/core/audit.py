"""Append-only audit log: every AI decision and human override goes here as JSONL.

This is a best-effort SECOND copy of decisions/extractions (the database is the primary
store). Writing here must never break a primary DB write, so log_event swallows I/O errors.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def log_event(
    audit_log_path: Path,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Append a single JSON line to the audit log. Creates parent dir and file if missing.

    Best-effort: a failure here is swallowed so the primary write is never compromised."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "payload": payload,
    }
    try:
        path = Path(audit_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def read_events(
    audit_log_path: Path,
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    """Read all events; optionally filter by event_type. Malformed lines are skipped."""
    path = Path(audit_log_path)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event_type is None or event.get("event_type") == event_type:
                events.append(event)
    return events
