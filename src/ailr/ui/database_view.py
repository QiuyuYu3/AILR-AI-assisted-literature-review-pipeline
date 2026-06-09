"""Database tab: read-only raw table browser. Inspect any table (incl. test_* and extractions)."""

from typing import Any

import dash_ag_grid as dag
import dash_bootstrap_components as dbc
from dash import Input, Output, html, no_update

from ailr.ui._common import get_project

_TABLE_OPTIONS = [
    {"label": "sources", "value": "sources"},
    {"label": "screening_decisions", "value": "screening_decisions"},
    {"label": "extractions (AI/human field values)", "value": "extractions"},
    {"label": "calibration_samples", "value": "calibration_samples"},
    {"label": "test_runs (quick-test runs)", "value": "test_runs"},
    {"label": "test_decisions (quick screening test)", "value": "test_decisions"},
    {"label": "test_extractions (quick extraction test)", "value": "test_extractions"},
    {"label": "prompt_versions", "value": "prompt_versions"},
    {"label": "reconciliations", "value": "reconciliations"},
    {"label": "api_calls", "value": "api_calls"},
    {"label": "screening_actions", "value": "screening_actions"},
    {"label": "notes", "value": "notes"},
    {"label": "tags", "value": "tags"},
    {"label": "source_tags", "value": "source_tags"},
    {"label": "duplicates", "value": "duplicates"},
    {"label": "exclusion_reasons", "value": "exclusion_reasons"},
]


def layout() -> Any:
    return html.Div(
        [
            html.H4("Database"),
            html.P("Read-only view of the raw tables — including the isolated test_* tables and AI extraction values. Showing up to 500 rows per table.", className="text-muted small"),
            dbc.Row(
                [
                    dbc.Col(
                        [dbc.Label("Table", className="small fw-bold mb-0"),
                         dbc.Select(id="db-table", options=_TABLE_OPTIONS, value="extractions", size="sm")],
                        width=4,
                    ),
                    dbc.Col(html.Div(id="db-count", className="text-muted small"), width="auto", className="align-self-end"),
                ],
                className="g-2 mb-2 align-items-end",
            ),
            dag.AgGrid(
                id="db-grid",
                columnDefs=[],
                rowData=[],
                defaultColDef={"resizable": True, "sortable": True, "filter": True, "minWidth": 110},
                dashGridOptions={"animateRows": True, "enableCellTextSelection": True, "domLayout": "autoHeight"},
                style={"height": None},
            ),
        ]
    )


def register_callbacks(app: Any) -> None:
    @app.callback(
        Output("db-grid", "columnDefs"),
        Output("db-grid", "rowData"),
        Output("db-count", "children"),
        Input("db-table", "value"),
    )
    def _load(table):
        if not table:
            return no_update, no_update, no_update
        db = get_project().db
        try:
            cols, rows = db.raw_table(table)
            total = db.table_count(table)
        except Exception as e:
            return [], [], f"Error: {e}"
        col_defs = [{"field": c, "headerName": c, "tooltipField": c} for c in cols]
        shown = len(rows)
        note = f"{total} row(s)" + (f" — showing first {shown}" if total > shown else "")
        return col_defs, rows, note
