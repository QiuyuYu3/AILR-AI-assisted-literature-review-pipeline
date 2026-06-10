"""Import tab: bring bibliographic references into the project (drag-and-drop).

Stage-specific imports live with their stage: Link PDFs (Zotero RIS) + Import markdown are on the
Full-text review tab; Import AI extraction results is on the Extraction tab.
"""

import base64
import tempfile
from pathlib import Path
from typing import Any

import dash_bootstrap_components as dbc
from dash import Input, Output, State, dcc, html, no_update

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
            dcc.Upload(
                id="import-ref-upload",
                children=html.Div(["Drag and drop, or ", html.A("select a file")]),
                multiple=False,
                style={"width": "100%", "height": "120px", "lineHeight": "120px", "borderWidth": "1px", "borderStyle": "dashed", "borderRadius": "6px", "textAlign": "center"},
                className="mb-3",
            ),
            dcc.Loading(html.Div(id="import-ref-feedback")),
            html.Hr(className="my-3"),
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
        Input("import-ref-upload", "contents"),
        State("import-ref-upload", "filename"),
        State("import-ref-db", "value"),
        State("import-ref-db-custom", "value"),
        prevent_initial_call=True,
    )
    def _import_refs(contents, filename, db_choice, db_custom):
        if not contents or not filename:
            return no_update
        source_db = _resolve_source_db(db_choice, db_custom)
        if source_db is _UNSET:
            return dbc.Alert("Select a source database before importing.", color="warning")
        ext = Path(filename).suffix.lower()
        if ext not in _SUPPORTED:
            return dbc.Alert(f"Unsupported file type: {ext or '(none)'}. Use RIS / BibTeX / CSV.", color="warning")
        try:
            _, content_string = contents.split(",", 1)
            decoded = base64.b64decode(content_string)
        except Exception:
            return dbc.Alert("Could not read the uploaded file.", color="danger")
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(decoded)
            tmp_path = Path(tmp.name)
        try:
            r = get_project().ingest(tmp_path, source_database=source_db)
        except AILRError as e:
            return dbc.Alert(f"Import failed: {e}", color="danger")
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

        body: list[Any] = [
            html.Strong(f"Imported {r.imported} new record(s) from {filename}."),
            html.Ul([
                html.Li(f"Parsed: {r.parsed}"),
                html.Li(f"Imported: {r.imported}"),
                html.Li(f"Deduplicated: {r.deduplicated} (see the Duplicates tab)"),
            ]),
        ]
        if r.failed:
            body.append(html.Div(f"{r.failed} record(s) failed to import.", className="text-danger small"))
        body.append(html.Small("Open Sources or Screening to see the new records.", className="text-muted"))
        return dbc.Alert(body, color="success")
