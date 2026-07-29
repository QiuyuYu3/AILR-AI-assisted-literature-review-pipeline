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


def _source_extraction_rows(db: Any, source_id: int, extractor_type: str) -> list[dict]:
    """Rows for one source. `final` means the adjudicated record when there is one, else the raw
    human record(s) — so a reconciled paper exports as one agreed row, and an unreconciled one
    still exports every reviewer separately rather than silently picking a winner."""
    if extractor_type != "final":
        return db.list_extractions(source_id, extractor_type=extractor_type)
    consensus = db.list_extractions(source_id, extractor_type="consensus")
    return consensus or db.list_extractions(source_id, extractor_type="human")


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


def extraction_table_rows(
    project: Project,
    *,
    extractor_type: str = "ai",
    only_includes: bool = True,
) -> tuple[list[str], list[dict[str, str]]]:
    """Return (column_names, rows). Used by CSV export and JSON-flat export."""
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

    db = project.db
    pid = project.project_id
    if only_includes:
        sources = db.list_includes_with_markdown(pid)
    else:
        sources = db.list_sources_with_markdown(pid)

    rows: list[dict[str, str]] = []
    for src in sources:
        # Re-pair each leaf value with its verbatim quote (stored in a separate column) so the
        # <field>_quote columns are populated; nested fields keep their inner structure.
        for extractor_id, ex_by_field in _group_by_extractor(_source_extraction_rows(db, src.id, extractor_type)).items():
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
    columns, rows = extraction_table_rows(project, extractor_type=extractor_type, only_includes=only_includes)
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
    db = project.db
    pid = project.project_id
    if only_includes:
        sources = db.list_includes_with_markdown(pid)
    else:
        sources = db.list_sources_with_markdown(pid)

    out: list[dict[str, Any]] = []
    for src in sources:
        # Leaf fields store the value and its verbatim quote separately; re-pair them as
        # {value, quote} so the JSON is self-contained. Nested fields already carry quotes inside.
        flag_check = None
        rows = _source_extraction_rows(db, src.id, extractor_type)
        # Under "final" the rows may be either the adjudicated record or the raw human ones;
        # report which, rather than the word the caller asked with.
        actual_type = rows[0]["extractor_type"] if rows else extractor_type
        grouped = _group_by_extractor(rows)
        for extractor_id, fields in grouped.items():
            if flag_check is None:
                flag_check = db.get_flag_check(src.id, extractor_type=actual_type)
            out.append(
                {
                    "source_id": src.id,
                    "first_author_year": _short_author_year(src),
                    "year": src.year,
                    "doi": src.doi,
                    "title": src.title,
                    "extractor_type": actual_type,
                    "extractor_id": extractor_id,
                    "fields": fields,
                    "flag_check": flag_check,
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

    records = _extraction_records(project, extractor_type=extractor_type, only_includes=only_includes)
    per_source: dict[int, int] = {}
    for rec in records:
        per_source[rec["source_id"]] = per_source.get(rec["source_id"], 0) + 1

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rec in records:
            sid = rec["source_id"]
            # Two reviewers on one paper (independent extraction) would collide on <source_id>.json.
            if per_source[sid] > 1:
                safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(rec.get("extractor_id") or "unknown"))
                fn = f"{sid}__{safe}.json"
            else:
                fn = f"{sid}.json"
            zf.writestr(fn, json.dumps(rec, indent=2, ensure_ascii=False))
    return buf.getvalue()


def extraction_rows_long(
    project: Project,
    *,
    extractor_type: str = "ai",
    only_includes: bool = True,
) -> list[dict[str, Any]]:
    """Long-format rows: one entry per (source, field). For ad-hoc analysis."""
    db = project.db
    pid = project.project_id
    if only_includes:
        sources = db.list_includes_with_markdown(pid)
    else:
        sources = db.list_sources_with_markdown(pid)

    out: list[dict[str, Any]] = []
    for src in sources:
        for row in _source_extraction_rows(db, src.id, extractor_type):
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
