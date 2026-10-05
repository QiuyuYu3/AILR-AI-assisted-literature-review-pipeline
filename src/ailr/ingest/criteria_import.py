"""Parse inclusion/exclusion criteria drafted by an external AI (a {"criteria": [...]} JSON object)."""

import json

from pydantic import ValidationError

from ailr.criteria import CriterionSpec
from ailr.ingest._report import ValidationReport, _short_error

_KEYS = {"id", "name", "pass_if", "fail_if", "uncertain_if"}


def _text(value):
    # null is JSON for "no rule"; padding on an ID would stop verdicts from matching it
    if value is None:
        return ""
    return value.strip() if isinstance(value, str) else value


def parse_criteria_import(raw: str) -> tuple[list[dict], ValidationReport]:
    report = ValidationReport()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        report.add("error", f"not valid JSON: {e}")
        return [], report

    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict) and isinstance(data.get("criteria"), list):
        rows = data["criteria"]
    else:
        report.add("error", 'expected an object with a "criteria" list (or a JSON array)')
        return [], report

    cleaned: list[dict] = []
    seen: set[str] = set()
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            report.add("error", f"criterion #{i + 1} is not an object")
            continue
        cf = {k: _text(r.get(k)) for k in _KEYS}
        label = next((v for v in (cf["name"], cf["id"]) if isinstance(v, str) and v), str(i + 1))
        for k in r:
            if k not in _KEYS:
                report.add("warning", f"ignored unknown key {k!r}", field=label)
        try:
            CriterionSpec(**cf)
        except ValidationError as e:
            report.add("error", _short_error(e), field=label)
            continue
        if not cf["name"] and not cf["pass_if"]:
            report.add("warning", "no name and no PASS rule", field=cf["id"] or str(i + 1))
        cid = cf["id"]
        if cid and cid in seen:
            report.add("error", "duplicate id", field=cid)
            continue
        if cid:
            seen.add(cid)
        cleaned.append(cf)
        report.ok_count += 1

    return cleaned, report
