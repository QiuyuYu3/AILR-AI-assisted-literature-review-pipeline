"""Structured inclusion/exclusion criteria: model, IO, stable IDs, and markdown rendering."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError

from ailr.exceptions import ConfigError


class CriterionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = ""
    name: str = ""
    pass_if: str = ""
    fail_if: str = ""
    uncertain_if: str = ""


class CriteriaSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criteria: list[CriterionSpec] = Field(default_factory=list)


def _detect_prefix(ids: list[str]) -> str:
    for i in ids:
        m = re.match(r"^([A-Za-z]+)\d+$", i)
        if m:
            return m.group(1)
    return "C"


def assign_ids(items: list[CriterionSpec]) -> list[CriterionSpec]:
    """Fill any blank IDs with the next free <prefix><n>, preserving IDs the user already set.
    Prefix is inferred from existing IDs (e.g. keeps a B1/B2… scheme), else 'C'."""
    existing = [c.id for c in items if c.id]
    prefix = _detect_prefix(existing)
    used = set(existing)
    n = 0
    for c in items:
        if not c.id:
            n += 1
            while f"{prefix}{n}" in used:
                n += 1
            c.id = f"{prefix}{n}"
            used.add(c.id)
    return items


def load_criteria(path: Path) -> CriteriaSet:
    if not path.exists():
        return CriteriaSet()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Failed to parse {path}: {e}") from e
    if isinstance(data, list):
        data = {"criteria": data}
    try:
        return CriteriaSet(**data)
    except PydanticValidationError as e:
        raise ConfigError(f"Invalid criteria in {path}:\n{e}") from e


def save_criteria(path: Path, items: list[dict]) -> CriteriaSet:
    """Validate, fill blank IDs, and write criteria to YAML. Returns the saved set."""
    specs = [CriterionSpec(**c) for c in items]
    assign_ids(specs)
    cs = CriteriaSet(criteria=specs)
    path.write_text(yaml.safe_dump(cs.model_dump(), sort_keys=False, allow_unicode=True), encoding="utf-8")
    return cs


def criterion_ids(cs: CriteriaSet) -> list[str]:
    return [c.id for c in cs.criteria if c.id]


def render_criteria_markdown(cs: CriteriaSet) -> str:
    """Render to the text injected as {{criteria}}: one block per criterion with its PASS/FAIL/UNCERTAIN rules."""
    blocks: list[str] = []
    for c in cs.criteria:
        head = f"{c.id}: {c.name}".strip().rstrip(":") if c.name else c.id
        lines = [head]
        if c.pass_if.strip():
            lines.append(f"  PASS if: {c.pass_if.strip()}")
        if c.fail_if.strip():
            lines.append(f"  FAIL if: {c.fail_if.strip()}")
        if c.uncertain_if.strip():
            lines.append(f"  UNCERTAIN if: {c.uncertain_if.strip()}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


_ID_RE = re.compile(r"^\s*([A-Za-z]{1,3}\d+)\s+(.+\S)\s*$")
# Accept "PASS if: …", "FAIL if …", "PASS requires …", "UNCERTAIN when: …" — keyword + optional connector.
_FIELD_RE = re.compile(r"^\s*(PASS|FAIL|UNCERTAIN)\b[ \t]*(?:if|requires?|when)?[ \t]*:?[ \t]*(.*)$", re.IGNORECASE)
_FIELD_KEY = {"PASS": "pass_if", "FAIL": "fail_if", "UNCERTAIN": "uncertain_if"}


def import_from_text(text: str) -> list[dict]:
    """Best-effort parse of a free-text criteria blob (the 'B1 … PASS if: … FAIL if: …' format)
    into criterion rows for the editor. The user reviews and saves."""
    items: list[dict] = []
    cur: dict | None = None
    field: str | None = None
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fm = _FIELD_RE.match(line)
        if fm and cur is not None:
            field = _FIELD_KEY[fm.group(1).upper()]
            cur[field] = (cur.get(field, "") + " " + fm.group(2).strip()).strip()
            continue
        im = _ID_RE.match(line)
        if im and not fm:
            if cur is not None:
                items.append(cur)
            cur = {"id": im.group(1), "name": im.group(2).strip(), "pass_if": "", "fail_if": "", "uncertain_if": ""}
            field = None
            continue
        if cur is not None and field is not None:  # continuation of the current field
            cur[field] = (cur.get(field, "") + " " + line.strip()).strip()
    if cur is not None:
        items.append(cur)
    return items


def resolve_criteria(root: Path, screening_cfg) -> tuple[str, list[str]]:
    """The single source of truth for a run: (criteria_text_for_prompt, criterion_ids).
    Prefers the structured criteria.yaml; falls back to the legacy free-text file (no IDs)."""
    structured = root / getattr(screening_cfg, "criteria_structured", "criteria.yaml")
    cs = load_criteria(structured)
    if cs.criteria:
        return render_criteria_markdown(cs), criterion_ids(cs)
    legacy = root / screening_cfg.criteria
    text = legacy.read_text(encoding="utf-8") if legacy.exists() else ""
    return text, []
