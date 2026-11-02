"""Extraction table export. Wide CSV (one row per source) and nested JSON.

Wide CSV strategy:
  - One row per included source.
  - For each schema field:
      leaf -> column `<field>` (value) and `<field>_quote` (verbatim quote).
      object -> flatten to `<field>.<sub>` and `<field>.<sub>_quote` per leaf.
      list-of-objects -> single column `<field>` with JSON-encoded list.
"""

import csv
import io
import json
import re
from typing import Any

from ailr.core.project import Project
from ailr.extraction import FieldSpec, compose_schema


def _flatten_columns(fields: list[FieldSpec]) -> list[tuple[str, FieldSpec, bool]]:
    """Return (column_name, owning_field_spec, is_leaf) tuples in CSV column order.

    is_leaf=True means a "_quote" companion column should be emitted next to it.
    """
    cols: list[tuple[str, FieldSpec, bool]] = []
    for f in fields:
        if f.type == "object":
            for sub in f.fields or []:
                cols.append((f"{f.name}.{sub.name}", sub, True))
        elif f.type == "list" and f.item_type == "object":
            cols.append((f.name, f, False))  # quotes live inside the items, not in source_quote
        elif f.type == "list":
            cols.append((f.name, f, True))   # list of scalars carries one source_quote for the field
        else:
            cols.append((f.name, f, True))
    return cols


def _cell_value(field_name: str, owning: FieldSpec, value: Any, *, is_leaf: bool) -> tuple[str, str]:
    """Return (value_cell, quote_cell). quote_cell is "" if not a leaf."""
    if value is None:
        return "", ""

    if not is_leaf:
        if isinstance(value, dict) and "value" in value:
            value = value.get("value")
        try:
            return json.dumps(value, ensure_ascii=False), ""
        except (TypeError, ValueError):
            return str(value), ""

    if isinstance(value, dict) and "value" in value:
        v = value.get("value")
        q = value.get("quote") or ""
    else:
        v = value
        q = ""

    if isinstance(v, (dict, list)):
        try:
            v_str = json.dumps(v, ensure_ascii=False)
        except (TypeError, ValueError):
            v_str = str(v)
    elif v is None:
        v_str = ""
    else:
        v_str = str(v)
    return v_str, q


def _extraction_rows_by_source(db: Any, project_id: int, extractor_type: str) -> dict[int, list[dict]]:
    """Rows per source, for the whole project at once. `final` means the adjudicated record when
    there is one, else the submitted human record(s) — so a reconciled paper exports as one agreed
    row, and an unreconciled one still exports every reviewer separately rather than silently
    picking a winner. A draft nobody has submitted is work in progress, not a record, and stays out."""
    if extractor_type != "final":
        return db.list_project_extractions(project_id, extractor_type)
    out = db.list_project_extractions(project_id, "consensus")
    for sid, human in db.list_project_extractions(project_id, "human").items():
        if sid not in out:
            submitted = {r["extractor_id"] for r in human if r["field_name"] == "_submitted"}
            out[sid] = [r for r in human if r["extractor_id"] in submitted]
    return out


def _group_by_extractor(ex_rows: list[dict]) -> dict[str, dict[str, Any]]:
    """{extractor_id: {field_name: {value, quote} | raw}}. Grouping by extractor matters because
    independent extraction leaves two humans' rows on the same paper — collapsing them into one
    record would silently keep whichever was written last, per field."""
    grouped: dict[str, dict[str, Any]] = {}
    for row in ex_rows:                      # ORDER BY id: within one extractor, later rows win
        # `_submitted` / other reserved markers are bookkeeping, not extracted values.
        if str(row["field_name"]).startswith("_"):
            continue
        fields = grouped.setdefault(row.get("extractor_id") or "", {})
        val = row["value"]
        quote = row.get("source_quote")
        if isinstance(val, dict):
            fields[row["field_name"]] = val      # object field: quotes sit at its leaves
        elif isinstance(val, list):
            # A list of scalars keeps its quote in source_quote, so re-pair it. Lists of objects
            # have none and stay bare, keeping their exported shape unchanged.
            fields[row["field_name"]] = {"value": val, "quote": quote} if quote else val
        else:
            fields[row["field_name"]] = {"value": val, "quote": quote}
    return grouped


def _export_sources(project: Project, only_includes: bool) -> list:
    """The papers an export covers: the included studies, as PRISMA's included box counts them, or
    every paper with a full text. Flagged duplicates are in neither."""
    sources = project.db.list_sources_with_markdown(project.project_id)
    if not only_includes:
        return sources
    included = project.db.final_include_ids(
        project.project_id, "full_text", workflow=project.config.screening_workflow("full_text"))
    return [s for s in sources if s.id in included]


def extraction_table_rows(
    project: Project,
    *,
    extractor_type: str = "ai",
    only_includes: bool = True,
) -> tuple[list[str], list[dict[str, str]]]:
    """Return (column_names, rows). Used by CSV export and JSON-flat export."""
    by_source = _extraction_rows_by_source(project.db, project.project_id, extractor_type)
    return _table_rows(project, _export_sources(project, only_includes), by_source)


def _table_rows(project: Project, sources: list, by_source: dict[int, list[dict]]) -> tuple[list[str], list[dict[str, str]]]:
    schema_path = project.root / project.config.extraction.schema_path
    fields = compose_schema(schema_path)
    layout = _flatten_columns(fields)

    # base_cols are DB-only metadata. Anything that also appears in the schema
    # (citation / first_author_year / year / doi / journal) is left to the schema columns
    # so values come from the extraction, not the ingest record.
    # Bibliographic identity columns are joined from the `sources` record (NOT AI-extracted).
    base_cols = ["source_id", "extractor_id", "first_author_year", "year", "doi", "journal", "ingest_title"]
    field_cols: list[str] = []
    seen: set[str] = set(base_cols)
    for col_name, _, is_leaf in layout:
        if col_name not in seen:
            field_cols.append(col_name)
            seen.add(col_name)
        if is_leaf:
            qcol = f"{col_name}_quote"
            if qcol not in seen:
                field_cols.append(qcol)
                seen.add(qcol)

    columns = base_cols + field_cols

    rows: list[dict[str, str]] = []
    for src in sources:
        # Re-pair each leaf value with its verbatim quote (stored in a separate column) so the
        # <field>_quote columns are populated; nested fields keep their inner structure.
        for extractor_id, ex_by_field in _group_by_extractor(by_source.get(src.id, [])).items():
            out_row: dict[str, str] = {
                "source_id": str(src.id),
                "extractor_id": extractor_id,
                "first_author_year": _short_author_year(src),
                "year": str(src.year) if src.year else "",
                "doi": src.doi or "",
                "journal": src.journal or "",
                "ingest_title": src.title or "",
            }
            written: set[str] = set(out_row.keys())
            for col_name, owning, is_leaf in layout:
                if col_name in written:
                    continue
                value = _lookup_value(col_name, owning, ex_by_field)
                v_str, q_str = _cell_value(col_name, owning, value, is_leaf=is_leaf)
                out_row[col_name] = v_str
                written.add(col_name)
                if is_leaf:
                    qcol = f"{col_name}_quote"
                    if qcol not in written:
                        out_row[qcol] = q_str
                        written.add(qcol)
            rows.append(out_row)

    return columns, rows


def _lookup_value(col_name: str, owning: FieldSpec, ex_by_field: dict[str, Any]) -> Any:
    """Look up a column's value from the {field_name: value} dict produced by list_extractions."""
    if "." in col_name:
        top, sub = col_name.split(".", 1)
        top_val = ex_by_field.get(top)
        if not isinstance(top_val, dict):
            return None
        return top_val.get(sub)
    return ex_by_field.get(col_name)


def _short_author_year(src) -> str:
    if not src.authors:
        return f"({src.year})" if src.year else ""
    first = src.authors[0].split(",")[0]
    return f"{first} {src.year}" if src.year else first


def extraction_table_csv(
    project: Project,
    *,
    extractor_type: str = "ai",
    only_includes: bool = True,
) -> str:
    return _csv_text(*extraction_table_rows(project, extractor_type=extractor_type, only_includes=only_includes))


def _csv_text(columns: list[str], rows: list[dict[str, str]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()


def _extraction_records(
    project: Project,
    *,
    extractor_type: str = "ai",
    only_includes: bool = True,
) -> list[dict[str, Any]]:
    """Per-source extraction dicts preserving the full {value, quote} shape. Shared by the combined
    JSON export and the per-paper ZIP export."""
    by_source = _extraction_rows_by_source(project.db, project.project_id, extractor_type)
    return _records(project.db, _export_sources(project, only_includes), by_source)


def _records(db: Any, sources: list, by_source: dict[int, list[dict]]) -> list[dict[str, Any]]:
    # Under "final" the rows may be either the adjudicated record or the raw human ones;
    # report which, rather than the word the caller asked with.
    actual_type = {s.id: by_source[s.id][0]["extractor_type"] for s in sources if by_source.get(s.id)}
    flag_checks = {
        kind: db.get_flag_checks([sid for sid, k in actual_type.items() if k == kind], extractor_type=kind)
        for kind in set(actual_type.values())
    }

    out: list[dict[str, Any]] = []
    for src in sources:
        # Leaf fields store the value and its verbatim quote separately; re-pair them as
        # {value, quote} so the JSON is self-contained. Nested fields already carry quotes inside.
        for extractor_id, fields in _group_by_extractor(by_source.get(src.id, [])).items():
            kind = actual_type[src.id]
            out.append(
                {
                    "source_id": src.id,
                    "first_author_year": _short_author_year(src),
                    "year": src.year,
                    "doi": src.doi,
                    "title": src.title,
                    "extractor_type": kind,
                    "extractor_id": extractor_id,
                    "fields": fields,
                    "flag_check": flag_checks[kind].get(src.id),
                }
            )
    return out


def extraction_table_json(
    project: Project,
    *,
    extractor_type: str = "ai",
    only_includes: bool = True,
) -> str:
    """Nested JSON: one array of per-source dicts, each preserving full {value, quote} shape."""
    return json.dumps(
        _extraction_records(project, extractor_type=extractor_type, only_includes=only_includes),
        indent=2, ensure_ascii=False,
    )


def extraction_per_paper_zip(
    project: Project,
    *,
    extractor_type: str = "ai",
    only_includes: bool = True,
) -> bytes:
    """A ZIP with one <source_id>.json per paper (same content as extraction_table_json, split per file)."""
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fn, text in _per_paper_files(_extraction_records(project, extractor_type=extractor_type, only_includes=only_includes)):
            zf.writestr(fn, text)
    return buf.getvalue()


def _per_paper_files(records: list[dict[str, Any]]) -> list[tuple[str, str]]:
    per_source: dict[int, int] = {}
    for rec in records:
        per_source[rec["source_id"]] = per_source.get(rec["source_id"], 0) + 1

    files: list[tuple[str, str]] = []
    for rec in records:
        sid = rec["source_id"]
        # Two reviewers on one paper (independent extraction) would collide on <source_id>.json.
        if per_source[sid] > 1:
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(rec.get("extractor_id") or "unknown"))
            fn = f"{sid}__{safe}.json"
        else:
            fn = f"{sid}.json"
        files.append((fn, json.dumps(rec, indent=2, ensure_ascii=False)))
    return files


_BUNDLE_README = """\
included/  the studies in the PRISMA "included" box: settled as includes at full text,
           not flagged as duplicates and not in conflict
all/       every paper with a full text, whatever its screening outcome
           (records flagged as duplicates are left out of both)

final.csv, final.json   the record each paper ends with: the saved consensus, otherwise
                        each reviewer's submitted extraction
ai.csv, ai.json         the AI's extraction
ai_per_paper/           the AI's extraction, one JSON file per paper

Each CSV has a <field>_quote column beside every field.
"""


def extraction_bundle_zip(project: Project) -> bytes:
    """Every extraction export in one ZIP: the included studies and all papers, final and AI records."""
    import zipfile

    db = project.db
    everything = _export_sources(project, only_includes=False)
    included_ids = {s.id for s in _export_sources(project, only_includes=True)}
    # Fetched once per kind and shared by every file below, so the query count does not grow with papers.
    by_source = {kind: _extraction_rows_by_source(db, project.project_id, kind) for kind in ("final", "ai")}
    records = {kind: _records(db, everything, rows) for kind, rows in by_source.items()}

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", _BUNDLE_README)
        for folder, sources in (("included", [s for s in everything if s.id in included_ids]), ("all", everything)):
            ids = {s.id for s in sources}
            for kind in ("final", "ai"):
                zf.writestr(f"{folder}/{kind}.csv", _csv_text(*_table_rows(project, sources, by_source[kind])))
                kept = [r for r in records[kind] if r["source_id"] in ids]
                zf.writestr(f"{folder}/{kind}.json", json.dumps(kept, indent=2, ensure_ascii=False))
            for fn, text in _per_paper_files([r for r in records["ai"] if r["source_id"] in ids]):
                zf.writestr(f"{folder}/ai_per_paper/{fn}", text)
    return buf.getvalue()


def extraction_rows_long(
    project: Project,
    *,
    extractor_type: str = "ai",
    only_includes: bool = True,
) -> list[dict[str, Any]]:
    """Long-format rows: one entry per (source, field). For ad-hoc analysis."""
    by_source = _extraction_rows_by_source(project.db, project.project_id, extractor_type)

    out: list[dict[str, Any]] = []
    for src in _export_sources(project, only_includes):
        for row in by_source.get(src.id, []):
            out.append(
                {
                    "source_id": src.id,
                    "extractor_id": row.get("extractor_id"),
                    "first_author_year": _short_author_year(src),
                    "field_name": row["field_name"],
                    "value": row["value"],
                    "source_quote": row.get("source_quote"),
                }
            )
    return out
