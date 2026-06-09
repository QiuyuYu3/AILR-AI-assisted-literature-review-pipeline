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


def layout() -> Any:
    return html.Div(
        [
            html.H4("Import references"),
            html.P(
                "Upload a RIS / BibTeX / CSV export (WoS, Scopus, PubMed, Zotero…). "
                "Duplicates are detected automatically by DOI and fuzzy title.",
                className="text-muted small",
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
        prevent_initial_call=True,
    )
    def _import_refs(contents, filename):
        if not contents or not filename:
            return no_update
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
            r = get_project().ingest(tmp_path)
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
