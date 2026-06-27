"""Import tab: bring bibliographic references into the project (drag-and-drop).

Stage-specific imports live with their stage: Link PDFs (Zotero RIS) + Import markdown are on the
Full-text review tab; Import AI extraction results is on the Extraction tab.
"""

import base64
import tempfile
from pathlib import Path
from typing import Any

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update

from ailr.exceptions import AILRError
from ailr.ui._common import get_project

_SUPPORTED = (".ris", ".bib", ".csv", ".tsv", ".txt")

_DB_OPTIONS = [
    {"label": "Auto-detect (from file)", "value": "__auto__"},
    {"label": "Web of Science (WoS)", "value": "WoS"},
    {"label": "Scopus", "value": "Scopus"},
    {"label": "PubMed", "value": "PubMed"},
    {"label": "Embase", "value": "Embase"},
    {"label": "PsycINFO", "value": "PsycINFO"},
    {"label": "Other (type below)", "value": "__other__"},
]


_UNSET = object()


def _resolve_source_db(db_choice: Any, db_custom: Any) -> Any:
    custom = (db_custom or "").strip()
    if db_choice == "__other__":
        return custom or _UNSET
    if db_choice == "__auto__":
        return None  # fall back to detect_source_database
    if db_choice:
        return db_choice
    return custom or _UNSET


def _searches_list() -> Any:
    try:
        project = get_project()
        rows = project.db.list_search_strategies(project.project_id)
    except Exception:
        return html.Small("No searches recorded yet.", className="text-muted")
    if not rows:
        return html.Small("No searches recorded yet.", className="text-muted")
    items = []
    for r in rows:
        meta = " · ".join(
            x for x in [
                r.get("source_database"),
                r.get("date_searched"),
                (f"{r.get('records_found')} found" if r.get("records_found") is not None else None),
                (f"{r.get('records_imported')} imported" if r.get("records_imported") is not None else None),
            ] if x
        )
        items.append(
            html.Div(
                [
                    html.Div(
                        [
                            html.Strong(meta, className="small"),
                            dbc.Button("Delete", id={"type": "search-del", "id": r["id"]}, size="sm", color="link", className="p-0 text-danger ms-2"),
                        ],
                        className="d-flex align-items-center justify-content-between",
                    ),
                    html.Div(r.get("search_query") or "", className="text-muted small", style={"whiteSpace": "pre-wrap"}),
                    (html.Div(r.get("filters"), className="text-muted small fst-italic") if r.get("filters") else None),
                ],
                className="mb-2 pb-2 border-bottom",
            )
        )
    return html.Div(items)


def layout() -> Any:
    return html.Div(
        [
            html.H4("Import references"),
            html.P(
                "Upload a RIS / BibTeX / CSV export (WoS, Scopus, PubMed, Zotero…). "
                "Duplicates are detected automatically by DOI and fuzzy title.",
                className="text-muted small",
            ),
            dbc.Label("Source database", className="fw-bold small"),
            dcc.Dropdown(id="import-ref-db", options=_DB_OPTIONS, placeholder="Required — select before importing", className="mb-2"),
            dbc.Input(id="import-ref-db-custom", placeholder="Custom database name", className="mb-3", style={"maxWidth": "320px"}),
            dbc.Label("Search details (optional — archived for your methods / PRISMA)", className="fw-bold small"),
            dbc.Textarea(id="import-ref-query", placeholder="Search query / strategy for this database", style={"height": "70px"}, className="mb-2"),
            dbc.Row(
                [
                    dbc.Col(dbc.Input(id="import-ref-date", type="date"), width="auto"),
                    dbc.Col(dbc.Input(id="import-ref-filters", placeholder="Filters / limits (years, language, doc type)"), width=True),
                ],
                className="mb-3 g-2",
            ),
            dcc.Upload(
                id="import-ref-upload",
                children=html.Div(["Drag and drop, or ", html.A("select a file")]),
                multiple=False,
                style={"width": "100%", "height": "120px", "lineHeight": "120px", "borderWidth": "1px", "borderStyle": "dashed", "borderRadius": "6px", "textAlign": "center"},
                className="mb-3",
            ),
            dcc.Loading(html.Div(id="import-ref-feedback")),
            html.Hr(className="my-3"),
            html.H6("Recorded searches", className="small fw-bold"),
            html.Div(_searches_list(), id="import-searches-list", className="mb-3"),
            html.Small(
                "Next steps live on their own tabs: link PDFs + convert/import markdown on Full-text review; "
                "import AI extraction results on Extraction.",
                className="text-muted",
            ),
        ]
    )


def register_callbacks(app: Any) -> None:
    @app.callback(
        Output("import-ref-feedback", "children"),
        Output("import-searches-list", "children", allow_duplicate=True),
        Input("import-ref-upload", "contents"),
        State("import-ref-upload", "filename"),
        State("import-ref-db", "value"),
        State("import-ref-db-custom", "value"),
        State("import-ref-query", "value"),
        State("import-ref-date", "value"),
        State("import-ref-filters", "value"),
        prevent_initial_call=True,
    )
    def _import_refs(contents, filename, db_choice, db_custom, query, date_searched, filters):
        if not contents or not filename:
            return no_update, no_update
        source_db = _resolve_source_db(db_choice, db_custom)
        if source_db is _UNSET:
            return dbc.Alert("Select a source database before importing.", color="warning"), no_update
        ext = Path(filename).suffix.lower()
        if ext not in _SUPPORTED:
            return dbc.Alert(f"Unsupported file type: {ext or '(none)'}. Use RIS / BibTeX / CSV.", color="warning"), no_update
        try:
            _, content_string = contents.split(",", 1)
            decoded = base64.b64decode(content_string)
        except Exception:
            return dbc.Alert("Could not read the uploaded file.", color="danger"), no_update
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(decoded)
            tmp_path = Path(tmp.name)
        project = get_project()
        try:
            r = project.ingest(tmp_path, source_database=source_db)
        except AILRError as e:
            return dbc.Alert(f"Import failed: {e}", color="danger"), no_update
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

        searches_out: Any = no_update
        if (query or "").strip():
            project.db.add_search_strategy(
                project.project_id,
                source_db or "Auto-detect",
                query.strip(),
                (date_searched or None),
                ((filters or "").strip() or None),
                r.parsed,
                r.imported,
            )
            searches_out = _searches_list()

        body: list[Any] = [
            html.Strong(f"Imported {r.imported} new record(s) from {filename}."),
            html.Ul([
                html.Li(f"Parsed: {r.parsed}"),
                html.Li(f"Imported: {r.imported}"),
                html.Li(f"Deduplicated: {r.deduplicated} (see the Duplicates tab)"),
            ]),
        ]
        if r.failed:
            body.append(html.Div(f"{r.failed} record(s) failed to import:", className="text-danger small mt-1"))
            body.append(html.Ul(
                [html.Li(f"{(f.get('title') or '(no title)')[:80]} — {f.get('error', '')}", className="small text-danger")
                 for f in r.failures[:10]],
                className="mb-0",
            ))
        missing_doi = project.db.count_sources_missing_doi(project.project_id)
        if missing_doi:
            body.append(html.Div(
                f"{missing_doi} source(s) in this project have no DOI — DOI is the stable key for de-duplication, "
                "PDF linking and exports. Add them on the Sources tab (“Edit metadata”).",
                className="text-warning small mt-1",
            ))
        body.append(html.Small("Open Sources or Screening to see the new records.", className="text-muted"))
        return dbc.Alert(body, color="success"), searches_out

    @app.callback(
        Output("import-searches-list", "children", allow_duplicate=True),
        Input({"type": "search-del", "id": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def _delete_search(_clicks):
        trig = ctx.triggered_id
        if not isinstance(trig, dict) or not any(c.get("value") for c in (ctx.triggered or [])):
            return no_update
        get_project().db.delete_search_strategy(trig["id"])
        return _searches_list()
