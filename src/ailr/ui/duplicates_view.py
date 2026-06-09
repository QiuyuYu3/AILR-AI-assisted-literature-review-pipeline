"""Duplicates tab: ingest-dropped duplicates (audit trail) + manually-flagged sources, both as tables."""

from typing import Any

import dash_ag_grid as dag
import dash_bootstrap_components as dbc
from dash import Input, Output, State, html, no_update

from ailr.ui._common import format_authors, get_project

_INGEST_COLS = [
    {"field": "id", "headerName": "ID", "width": 80},
    {"field": "title", "headerName": "Title", "flex": 3, "tooltipField": "title"},
    {"field": "authors", "headerName": "Authors", "flex": 2, "tooltipField": "authors"},
    {"field": "doi", "headerName": "DOI", "flex": 1, "cellRenderer": "markdown"},
    {"field": "reason", "headerName": "Detected by", "width": 160},
    {"field": "matched_source_id", "headerName": "Matched source", "width": 140},
    {"field": "detected_at", "headerName": "Detected at", "width": 180},
]

_MANUAL_COLS = [
    {"field": "id", "headerName": "ID", "width": 90, "checkboxSelection": True, "headerCheckboxSelection": True},
    {"field": "title", "headerName": "Title", "flex": 3, "tooltipField": "title"},
    {"field": "authors", "headerName": "Authors", "flex": 2, "tooltipField": "authors"},
    {"field": "doi", "headerName": "DOI", "flex": 1, "cellRenderer": "markdown"},
    {"field": "year", "headerName": "Year", "width": 100},
]


def _doi_link(doi: object) -> str:
    return f"[{doi}](https://doi.org/{doi})" if doi else ""


def _manual_rows() -> list[dict]:
    project = get_project()
    rows = project.db.list_manual_duplicates(project.project_id)
    for r in rows:
        r["authors"] = format_authors(r.get("authors"))
        r["doi"] = _doi_link(r.get("doi"))
    return rows


def _ingest_rows() -> list[dict]:
    project = get_project()
    rows = project.db.list_duplicates(project.project_id)
    for r in rows:
        r["authors"] = format_authors(r.get("authors"))
        r["doi"] = _doi_link(r.get("doi"))
    return rows


def layout() -> Any:
    ingest_rows = _ingest_rows()

    return html.Div(
        [
            html.H4("Duplicates"),
            html.H6("Manually flagged", className="mt-2"),
            html.P(
                "Flagged via the 'Duplicate' button on a screening/full-text card. "
                "Select rows and Restore to bring them back.",
                className="text-muted small",
            ),
            dbc.Button("Restore selected", id="dup-restore", color="secondary", outline=True, size="sm", className="mb-2"),
            html.Div(id="dup-restore-feedback", className="small mb-2"),
            dag.AgGrid(
                id="dup-manual-grid",
                rowData=_manual_rows(),
                columnDefs=_MANUAL_COLS,
                defaultColDef={"sortable": True, "filter": True, "resizable": True},
                dashGridOptions={"rowSelection": "multiple", "suppressRowClickSelection": True, "domLayout": "autoHeight", "enableCellTextSelection": True},
                columnSize="sizeToFit",
            ),
            html.Hr(className="my-3"),
            html.H6("Removed at import"),
            html.P(
                f"{len(ingest_rows)} record(s) dropped during ingest (DOI or fuzzy title; never imported as sources).",
                className="text-muted small",
            ),
            dag.AgGrid(
                rowData=ingest_rows,
                columnDefs=_INGEST_COLS,
                defaultColDef={"sortable": True, "filter": True, "resizable": True},
                dashGridOptions={"pagination": True, "paginationPageSize": 25, "enableCellTextSelection": True},
                style={"height": "45vh"},
            ),
        ]
    )


def register_callbacks(app: Any) -> None:
    @app.callback(
        Output("dup-manual-grid", "rowData"),
        Output("dup-restore-feedback", "children"),
        Input("dup-restore", "n_clicks"),
        State("dup-manual-grid", "selectedRows"),
        prevent_initial_call=True,
    )
    def _restore(_clicks, selected):
        if not selected:
            return no_update, dbc.Alert("Select one or more rows first.", color="warning", className="mb-0 py-1")
        db = get_project().db
        n = 0
        for row in selected:
            sid = row.get("id")
            if sid is not None:
                db.mark_source_duplicate(int(sid), False)
                n += 1
        return _manual_rows(), dbc.Alert(f"Restored {n} source(s).", color="success", className="mb-0 py-1")
