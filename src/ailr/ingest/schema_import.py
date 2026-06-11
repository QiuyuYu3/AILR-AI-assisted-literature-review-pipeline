"""Import a variable definition (the extraction schema) drafted by an external AI.

Expected JSON: an object {"fields": [ ... ]} (a bare array is also accepted), where each field is
{name, type, description?, required?, enum?, item_type?, item_fields?, fields?} — the same shape as
FieldSpec. Nothing is written; the caller loads the returned fields into the Template editor for review.
"""

import json

from pydantic import ValidationError

from ailr.extraction import FieldSpec
from ailr.ingest._report import ValidationReport

_FIELD_KEYS = {"name", "type", "description", "enum", "required", "multi", "verify", "item_type", "item_fields", "fields"}


def parse_schema_import(raw: str) -> tuple[list[dict], ValidationReport]:
    """Parse a variable-definition JSON string into editor-ready field dicts + a validation report."""
    report = ValidationReport()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        report.add("error", f"not valid JSON: {e}")
        return [], report

    if isinstance(data, list):
        raw_fields = data
    elif isinstance(data, dict) and isinstance(data.get("fields"), list):
        raw_fields = data["fields"]
    else:
        report.add("error", 'expected an object with a "fields" list (or a JSON array of fields)')
        return [], report

    cleaned: list[dict] = []
    seen: set[str] = set()
    for i, f in enumerate(raw_fields):
        if not isinstance(f, dict):
            report.add("error", f"field #{i + 1} is not an object")
            continue
        cf = _clean_field(f, report)
        name = cf.get("name")
        try:
            FieldSpec(**cf)
        except ValidationError as e:
            report.add("error", _short_error(e), field=name if isinstance(name, str) else None)
            continue
        if name in seen:
            report.add("error", "duplicate field name", field=name)
            continue
        seen.add(name)
        _soft_checks(cf, report)
        cleaned.append(cf)
        report.ok_count += 1

    return cleaned, report


def _clean_field(f: dict, report: ValidationReport) -> dict:
    name = f.get("name")
    out: dict = {}
    for k, v in f.items():
        if k in _FIELD_KEYS:
            out[k] = v
        else:
            report.add("warning", f"ignored unknown key {k!r}", field=name if isinstance(name, str) else None)
    for sub_key in ("fields", "item_fields"):
        if isinstance(out.get(sub_key), list):
            out[sub_key] = [_clean_field(s, report) for s in out[sub_key] if isinstance(s, dict)]
    return out


def _soft_checks(cf: dict, report: ValidationReport) -> None:
    name = cf.get("name")
    if not cf.get("description"):
        report.add("warning", "no description (the AI reads this as the field's label)", field=name)
    if cf.get("enum") and cf.get("type") != "string":
        report.add("warning", "enum is only used on text fields", field=name)
    if cf.get("type") == "object" and not cf.get("fields"):
        report.add("warning", "object field has no sub-fields", field=name)
    if cf.get("type") == "list" and cf.get("item_type") == "object" and not cf.get("item_fields"):
        report.add("warning", "repeating group has no sub-fields", field=name)


def _short_error(e: ValidationError) -> str:
    errs = e.errors()
    if not errs:
        return "invalid field"
    first = errs[0]
    loc = ".".join(str(x) for x in first.get("loc", ()))
    msg = first.get("msg", "invalid")
    return f"{loc}: {msg}" if loc else msg
