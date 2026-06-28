"""Extraction schema: model + composition (core + suggested + user) and JSON-Schema tool generation.

When `with_quotes=True`, every leaf field (string/integer/number/boolean) is wrapped:
    "name": {
        "type": "object",
        "properties": {
            "value": <original leaf schema>,
            "quote": {"type": ["string", "null"], "description": "verbatim quote from the paper"}
        },
        "required": ["value"]
    }
This is the canonical pattern used by the v2 extraction prompt.
"""

from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError

from ailr.exceptions import ConfigError, InputNotFoundError
from ailr.llm.base import ToolSchema


class FieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    type: Literal["string", "integer", "number", "boolean", "list", "object"]
    description: Optional[str] = None
    enum: Optional[list[str]] = None
    required: bool = False
    multi: bool = False
    core: bool = False
    verify: bool = True  # whether a human must verify this field at extraction (False = accept AI value)
    item_type: Optional[Literal["string", "integer", "number", "boolean", "object"]] = None
    fields: Optional[list["FieldSpec"]] = None
    item_fields: Optional[list["FieldSpec"]] = None


FieldSpec.model_rebuild()


class UserSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_core: bool = True
    include_suggested: Union[Literal["all"], list[str]] = Field(default_factory=list)
    fields: list[FieldSpec] = Field(default_factory=list)
    skip_verify: list[str] = Field(default_factory=list)  # field names to accept from AI without human review


def load_core_schema() -> list[FieldSpec]:
    """Load the package's built-in core_schema.yaml as a list of FieldSpec."""
    text = (files("ailr") / "core_schema.yaml").read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Built-in core_schema.yaml is malformed: {e}") from e
    return [FieldSpec(**f) for f in data.get("fields", [])]


def load_user_schema(path: Path) -> UserSchema:
    if not path.exists():
        raise InputNotFoundError(f"schema file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Failed to parse {path}: {e}") from e
    try:
        return UserSchema(**data)
    except PydanticValidationError as e:
        raise ConfigError(f"Invalid schema in {path}:\n{e}") from e


def save_user_schema(
    path: Path,
    include_core: bool,
    include_suggested: list[str],
    fields: list[dict],
    skip_verify: Optional[list[str]] = None,
) -> None:
    """Write a UserSchema back to YAML. `fields` are validated through FieldSpec first."""
    specs = [FieldSpec(**f) for f in fields]  # raises ConfigError-worthy ValidationError on bad input
    data = {
        "include_core": bool(include_core),
        "include_suggested": list(include_suggested or []),
        "skip_verify": list(skip_verify or []),
        "fields": [s.model_dump(exclude_none=True, exclude_defaults=True) for s in specs],
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


# Fields that legacy `include_core: true` pulls in (these used to be tier "core").
# Bibliographic fields (year/doi/journal/…) live in the `sources` table from import
# and are joined into exports by source_id — they are not AI-extracted. `include_core`
# therefore pulls in nothing by default; users add the fields they want in the Template tab.
_LEGACY_CORE_NAMES: list[str] = []


def compose_schema(user_schema_path: Path) -> list[FieldSpec]:
    """Build the final list of extraction fields: suggested modules (by name) + user fields.

    `include_core: true` is honoured for backward compat (pulls in the common bibliographic
    fields). Duplicate names (user redefines a field) are resolved user-wins.
    """
    user = load_user_schema(user_schema_path)
    core_fields = load_core_schema()

    catalog: list[FieldSpec] = []
    seen_names: set[str] = set()

    suggested_by_name = {f.name: f for f in core_fields if not f.core}

    if user.include_core:  # legacy: add the common bibliographic fields by name
        for name in _LEGACY_CORE_NAMES:
            if name in suggested_by_name and name not in seen_names:
                catalog.append(suggested_by_name[name])
                seen_names.add(name)

    if user.include_suggested == "all":
        for f in suggested_by_name.values():
            if f.name not in seen_names:
                catalog.append(f)
                seen_names.add(f.name)
    elif isinstance(user.include_suggested, list):
        # Unknown names are skipped (not an error) so removing a built-in field never
        # breaks an existing project's schema.yaml that still references it.
        for name in user.include_suggested:
            if name in suggested_by_name and name not in seen_names:
                catalog.append(suggested_by_name[name])
                seen_names.add(name)

    # User fields override existing entries with the same name (user-wins).
    user_names = {f.name for f in user.fields}
    catalog = [f for f in catalog if f.name not in user_names]
    catalog.extend(user.fields)

    # Apply human-verify selection: fields named in skip_verify are accepted from AI
    # without human review.
    skip = set(user.skip_verify or [])
    for f in catalog:
        f.verify = f.name not in skip
    return catalog


def schema_to_markdown(fields: list[FieldSpec]) -> str:
    """Render the composed schema as markdown. Used by `ailr export --format codebook`."""
    lines: list[str] = []
    for f in fields:
        _render_field_md(f, lines, indent=0)
    return "\n".join(lines)


_PROMPT_LEFTOVER = re.compile(r"\{\{[^}]+\}\}")


def compose_prompt(template: str, **values: str) -> str:
    """Fill {{key}} placeholders, then drop any leftover {{...}} so they don't leak into the prompt.

    Shared by the reviewers (what is sent to the LLM) and the UI preview so the two never drift.
    """
    composed = template or ""
    for key, value in values.items():
        composed = composed.replace("{{" + key + "}}", value)
    return _PROMPT_LEFTOVER.sub("", composed)


def compose_extraction_prompt(
    template: str,
    *,
    criteria: str = "",
    schema_md: str = "",
    additional: str = "",
) -> str:
    """Compose the full extraction system prompt from the fixed scaffold + the two user-edited
    parts (criteria, additional). Shared by the reviewer and the UI preview so they never drift.

    If the scaffold has no {{additional}} marker (older projects), non-empty additional
    instructions are appended at the end so they still take effect.
    """
    has_marker = "{{additional}}" in (template or "")
    composed = compose_prompt(
        template, criteria=criteria, schema_md=schema_md, schema_json=schema_md, additional=additional
    )
    if additional and additional.strip() and not has_marker:
        composed = composed.rstrip() + "\n\n# ADDITIONAL INSTRUCTIONS\n\n" + additional.strip()
    return composed


def _render_field_md(field: FieldSpec, lines: list[str], indent: int) -> None:
    prefix = "  " * indent + "- "
    req = " (required)" if field.required else ""
    desc = f" — {field.description}" if field.description else ""

    if field.type == "object":
        lines.append(f"{prefix}**{field.name}** (object{req}){desc}")
        for sub in field.fields or []:
            _render_field_md(sub, lines, indent + 1)
    elif field.type == "list":
        if field.item_type == "object":
            lines.append(f"{prefix}**{field.name}** (list of objects{req}){desc}")
            for sub in field.item_fields or []:
                _render_field_md(sub, lines, indent + 1)
        else:
            inner = field.item_type or "string"
            enum_str = f" [{' | '.join(field.enum)}]" if field.enum else ""
            lines.append(f"{prefix}**{field.name}** (list of {inner}{req}){enum_str}{desc}")
    else:
        enum_str = ""
        if field.enum:
            enum_str = f" [{' | '.join(field.enum)}]"
        lines.append(f"{prefix}**{field.name}** ({field.type}{req}){enum_str}{desc}")


_BASIC_TYPES = {
    "string": {"type": "string"},
    "integer": {"type": "integer"},
    "number": {"type": "number"},
    "boolean": {"type": "boolean"},
}


def build_extraction_tool_schema(
    fields: list[FieldSpec],
    *,
    with_quotes: bool = True,
    tool_name: str = "record_extraction",
    tool_description: str = "Record the structured extraction for the paper provided in the user message.",
) -> ToolSchema:
    properties: dict[str, dict[str, Any]] = {}
    required: list[str] = []
    for f in fields:
        properties[f.name] = _field_to_json_schema(f, with_quotes=with_quotes)
        if f.required:
            required.append(f.name)

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        input_schema["required"] = required

    return ToolSchema(name=tool_name, description=tool_description, input_schema=input_schema)


def _field_to_json_schema(field: FieldSpec, *, with_quotes: bool) -> dict[str, Any]:
    if field.type == "object":
        sub_props: dict[str, Any] = {}
        sub_required: list[str] = []
        for sub in field.fields or []:
            sub_props[sub.name] = _field_to_json_schema(sub, with_quotes=with_quotes)
            if sub.required:
                sub_required.append(sub.name)
        out: dict[str, Any] = {"type": "object", "properties": sub_props}
        if sub_required:
            out["required"] = sub_required
        if field.description:
            out["description"] = field.description
        return out

    if field.type == "list":
        if field.item_type == "object":
            item_props: dict[str, Any] = {}
            item_required: list[str] = []
            for sub in field.item_fields or []:
                item_props[sub.name] = _field_to_json_schema(sub, with_quotes=with_quotes)
                if sub.required:
                    item_required.append(sub.name)
            item_schema: dict[str, Any] = {"type": "object", "properties": item_props}
            if item_required:
                item_schema["required"] = item_required
        else:
            item_schema = dict(_BASIC_TYPES.get(field.item_type or "string", {"type": "string"}))
            if field.enum:  # constrain each list item to a fixed set (multi-select from options)
                item_schema["enum"] = field.enum

        arr: dict[str, Any] = {"type": "array", "items": item_schema}
        if field.description:
            arr["description"] = field.description
        return arr

    # Leaf
    leaf: dict[str, Any] = dict(_BASIC_TYPES.get(field.type, {"type": "string"}))
    if field.enum:
        leaf["enum"] = field.enum
    if field.description:
        leaf["description"] = field.description

    if with_quotes:
        return {
            "type": "object",
            "properties": {
                "value": leaf,
                "quote": {
                    "type": ["string", "null"],
                    "description": "Verbatim quote from the paper supporting this value, or null if not stated.",
                },
            },
            "required": ["value"],
        }
    return leaf
