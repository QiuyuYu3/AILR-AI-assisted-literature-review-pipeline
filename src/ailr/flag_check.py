"""The per-criterion verdicts a model returns, in the one shape the rest of the package reads."""

import json
from typing import Any


def normalize_flag_check(raw: Any) -> list[dict[str, Any]] | None:
    """Canonical list[dict] from the named-slot object, its flat {ID: verdict} form, or the legacy array."""
    if isinstance(raw, str):  # serialized into a JSON string, as models sometimes do
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if isinstance(raw, dict):
        raw = [{"criterion_id": cid, **(v if isinstance(v, dict) else {"verdict": v})} for cid, v in raw.items()]
    if not isinstance(raw, list):
        return None
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            verdict = item.get("verdict")
            out.append({**item, "verdict": verdict.strip().upper() if isinstance(verdict, str) else verdict})
    return out
