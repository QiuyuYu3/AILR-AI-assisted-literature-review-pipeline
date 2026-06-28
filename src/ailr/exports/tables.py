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
            cols.append((f.name, f, False))
        elif f.type == "list":
            cols.append((f.name, f, False))
        else:
            cols.append((f.name, f, True))
    return cols


def _cell_value(field_name: str, owning: FieldSpec, value: Any, *, is_leaf: bool) -> tuple[str, str]:
    """Return (value_cell, quote_cell). quote_cell is "" if not a leaf."""
    if value is None:
        return "", ""

    if not is_leaf:
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
    base_cols = ["source_id", "first_author_year", "year", "doi", "journal", "ingest_title"]
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
        ex_rows = db.list_extractions(src.id, extractor_type=extractor_type)
        if not ex_rows:
            continue
        # Re-pair each leaf value with its verbatim quote (stored in a separate column) so the
        # <field>_quote columns are populated; nested fields keep their inner structure.
        ex_by_field: dict[str, Any] = {}
        for row in ex_rows:
            val = row["value"]
            if isinstance(val, (dict, list)):
                ex_by_field[row["field_name"]] = val
            else:
                ex_by_field[row["field_name"]] = {"value": val, "quote": row.get("source_quote")}

        out_row: dict[str, str] = {
            "source_id": str(src.id),
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


def extraction_table_json(
    project: Project,
    *,
    extractor_type: str = "ai",
    only_includes: bool = True,
) -> str:
    """Nested JSON: per-source dict preserving full {value, quote} shape."""
    db = project.db
    pid = project.project_id
    if only_includes:
        sources = db.list_includes_with_markdown(pid)
    else:
        sources = db.list_sources_with_markdown(pid)

    out: list[dict[str, Any]] = []
    for src in sources:
        ex_rows = db.list_extractions(src.id, extractor_type=extractor_type)
        if not ex_rows:
            continue
        # Leaf fields store the value and its verbatim quote separately; re-pair them as
        # {value, quote} so the JSON is self-contained. Nested fields already carry quotes inside.
        fields: dict[str, Any] = {}
        for row in ex_rows:
            val = row["value"]
            if isinstance(val, (dict, list)):
                fields[row["field_name"]] = val
            else:
                fields[row["field_name"]] = {"value": val, "quote": row.get("source_quote")}
        flag_check = db.get_flag_check(src.id, extractor_type=extractor_type)
        out.append(
            {
                "source_id": src.id,
                "first_author_year": _short_author_year(src),
                "year": src.year,
                "doi": src.doi,
                "title": src.title,
                "extractor_type": extractor_type,
                "fields": fields,
                "flag_check": flag_check,
            }
        )
    return json.dumps(out, indent=2, ensure_ascii=False)


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
        for row in db.list_extractions(src.id, extractor_type=extractor_type):
            out.append(
                {
                    "source_id": src.id,
                    "first_author_year": _short_author_year(src),
                    "field_name": row["field_name"],
                    "value": row["value"],
                    "source_quote": row.get("source_quote"),
                }
            )
    return out
