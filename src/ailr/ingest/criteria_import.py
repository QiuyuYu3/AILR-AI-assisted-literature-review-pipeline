"""Parse inclusion/exclusion criteria drafted by an external AI (a {"criteria": [...]} JSON object)."""

import json

from pydantic import ValidationError

from ailr.criteria import CriterionSpec
from ailr.ingest._report import ValidationReport

_KEYS = {"id", "name", "pass_if", "fail_if", "uncertain_if"}


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
        cf = {k: r.get(k, "") for k in _KEYS}
        for k, v in r.items():
            if k not in _KEYS:
                report.add("warning", f"ignored unknown key {k!r}", field=str(cf.get("name") or i + 1))
        try:
            CriterionSpec(**cf)
        except ValidationError as e:
            report.add("error", str(e.errors()[0].get("msg", "invalid")), field=cf.get("name") or None)
            continue
        if not (cf["name"] or "").strip() and not (cf["pass_if"] or "").strip():
            report.add("warning", "no name and no PASS rule", field=cf.get("id") or str(i + 1))
        cid = (cf["id"] or "").strip()
        if cid and cid in seen:
            report.add("error", "duplicate id", field=cid)
            continue
        if cid:
            seen.add(cid)
        cleaned.append(cf)
        report.ok_count += 1

    return cleaned, report
