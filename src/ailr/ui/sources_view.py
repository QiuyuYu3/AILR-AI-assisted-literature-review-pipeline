"""Sources overview tab: ag-grid table of all sources with bulk decision tagging."""

from typing import Any

import dash_ag_grid as dag
import dash_bootstrap_components as dbc
from dash import Input, Output, State, dcc, html, no_update

from ailr.reviewers import ScreeningDecision
from ailr.ui._common import format_authors, get_project

_TAG_ACTIONS = [
    {"label": "Apply (add) tag", "value": "add"},
    {"label": "Remove tag", "value": "remove"},
]

_GRID_OPTIONS = {
    "rowSelection": "multiple",
    "domLayout": "autoHeight",
    "suppressRowClickSelection": True,
    "animateRows": True,
    "enableCellTextSelection": True,
}

_COLUMN_DEFS = [
    {
        "field": "id",
        "headerName": "ID",
        "width": 80,
        "checkboxSelection": True,
        "headerCheckboxSelection": True,
        "headerCheckboxSelectionFilteredOnly": True,
        "pinned": "left",
    },
    {"field": "year", "headerName": "Year", "width": 90, "filter": "agNumberColumnFilter"},
    {"field": "title", "headerName": "Title", "flex": 3, "tooltipField": "title"},
    {"field": "authors", "headerName": "Authors", "flex": 2, "tooltipField": "authors"},
    {"field": "journal", "headerName": "Journal", "flex": 1, "tooltipField": "journal"},
    {"field": "doi", "headerName": "DOI", "flex": 1, "cellRenderer": "markdown"},
    {"field": "source_database", "headerName": "DB", "width": 90},
    {"field": "tags", "headerName": "Tags", "flex": 1, "tooltipField": "tags"},
    {"field": "ai_decision", "headerName": "AI", "width": 100},
    {"field": "ai_confidence", "headerName": "AI conf", "width": 95, "filter": "agNumberColumnFilter"},
    {"field": "abstract_decision", "headerName": "Abstract", "width": 110},
    {"field": "full_text_decision", "headerName": "Full-text", "width": 110},
    {
        "field": "has_markdown",
        "headerName": "MD?",
        "width": 80,
        "valueFormatter": {"function": "params.value ? 'yes' : ''"},
    },
    {
        "field": "ai_extracted_fields",
        "headerName": "Extracted",
        "width": 110,
        "filter": "agNumberColumnFilter",
    },
]


_PRESETS = {
    "screening": ["year", "title", "authors", "tags", "ai_decision", "ai_confidence", "abstract_decision"],
    "fulltext": ["year", "title", "has_markdown", "full_text_decision", "ai_extracted_fields"],
    "biblio": ["year", "title", "authors", "journal", "doi", "source_database"],
    "all": [c["field"] for c in _COLUMN_DEFS if c["field"] != "id"],
}

_PRESET_OPTIONS = [
    {"label": "Screening view", "value": "screening"},
    {"label": "Full-text & extraction view", "value": "fulltext"},
    {"label": "Bibliographic view", "value": "biblio"},
    {"label": "All columns", "value": "all"},
]

_COLUMN_OPTIONS = [
    {"label": c.get("headerName", c["field"]), "value": c["field"]}
    for c in _COLUMN_DEFS if c["field"] != "id"
]


def _build_column_defs(selected: list[str]) -> list[dict]:
    """id column is always shown (selection + pinned); others follow the selection, in canonical order."""
    sel = set(selected or [])
    return [c for c in _COLUMN_DEFS if c["field"] == "id" or c["field"] in sel]


def _initial_overview_rows() -> list[dict]:
    project = get_project()
    rows = project.db.list_sources_overview(project.project_id)
    ids = [r["id"] for r in rows]
    tag_map = project.db.get_tags_for_sources(ids)
    for r in rows:
        tags = tag_map.get(r["id"], [])
        r["tags"] = ", ".join(t["name"] for t in tags) if tags else ""
        r["authors"] = format_authors(r.get("authors"))
        doi = r.get("doi")
        r["doi"] = f"[{doi}](https://doi.org/{doi})" if doi else ""
    return rows


def _initial_tag_select_options() -> list[dict]:
    project = get_project()
    tags = project.db.list_tags(project.project_id)
    if not tags:
        return [{"label": "(no tags — create some in Tags tab)", "value": ""}]
    return [{"label": "Pick a tag", "value": ""}] + [
        {"label": t["name"], "value": str(t["id"])} for t in tags
    ]


def _extract_modal() -> Any:
    return dbc.Modal(
        [
            dbc.ModalHeader(dbc.ModalTitle(id="sources-extract-title")),
            dbc.ModalBody(id="sources-extract-body"),
        ],
        id="sources-extract-modal",
        is_open=False,
        size="lg",
        scrollable=True,
    )


def _extraction_detail(source_id: int) -> Any:
    db = get_project().db
    src = db.get_source(source_id)
    rows = db.list_extractions(source_id, "ai")
    flag = db.get_flag_check(source_id, "ai")
    if not rows and not flag:
        return html.P("No AI extraction for this source yet.", className="text-muted")
    field_rows = []
    for r in rows:
        quote = r.get("source_quote")
        field_rows.append(
            html.Tr([
                html.Td(r.get("field_name"), className="fw-bold small", style={"whiteSpace": "nowrap"}),
                html.Td([
                    html.Div(str(r.get("value")) if r.get("value") is not None else "—", className="small"),
                    html.Div(f"“{quote}”", className="text-muted small fst-italic") if quote else None,
                ]),
                html.Td(f"{r['confidence']:.2f}" if r.get("confidence") is not None else "", className="small"),
            ])
        )
    table = dbc.Table(
        [html.Thead(html.Tr([html.Th("Field"), html.Th("Value / quote"), html.Th("Conf.")])), html.Tbody(field_rows)],
        bordered=False, hover=True, size="sm",
    ) if field_rows else None
    flag_block = None
    if flag:
        flag_block = html.Div(
            [
                html.H6("Flag check", className="mt-3"),
                html.Ul([html.Li(f"{f.get('criterion_id', '?')}: {f.get('verdict', '')} — {f.get('reason', '')}", className="small") for f in flag]),
            ]
        )
    return html.Div([html.Div(src.title if src else "", className="text-muted small mb-2"), table, flag_block])


def layout() -> Any:
    return html.Div([_extract_modal(), dbc.Row(
        [
            dbc.Col(
                [
                    html.H6("Filter"),
                    dbc.Label("Stage", className="small"),
                    dbc.Select(
                        id="sources-filter-stage",
                        options=[
                            {"label": "Abstract (human)", "value": "abstract_decision"},
                            {"label": "Full-text (human)", "value": "full_text_decision"},
                            {"label": "AI", "value": "ai_decision"},
                        ],
                        value="abstract_decision",
                        className="mb-1",
                    ),
                    dbc.Label("Decision", className="small"),
                    dbc.Select(
                        id="sources-filter-decision",
                        options=[
                            {"label": "(any)", "value": ""},
                            {"label": "include", "value": "include"},
                            {"label": "exclude", "value": "exclude"},
                            {"label": "uncertain", "value": "uncertain"},
                            {"label": "(not yet decided)", "value": "__none__"},
                        ],
                        value="",
                        className="mb-2",
                    ),
                    html.Hr(),

                    html.H6("Bulk action"),
                    dbc.Label("Stage"),
                    dbc.Select(
                        id="bulk-stage",
                        options=[
                            {"label": "Abstract screening", "value": "abstract"},
                            {"label": "Full-text screening", "value": "full_text"},
                        ],
                        value="abstract",
                        className="mb-2",
                    ),
                    dbc.Label("Decision"),
                    dbc.Select(
                        id="bulk-decision",
                        options=[
                            {"label": "include", "value": "include"},
                            {"label": "exclude", "value": "exclude"},
                            {"label": "uncertain", "value": "uncertain"},
                        ],
                        value="exclude",
                    ),
                    dbc.Label("Reasoning (applied to all)", className="mt-2"),
                    dbc.Textarea(
                        id="bulk-reasoning",
                        placeholder="e.g. wrong population, not dyadic, ...",
                        style={"height": "80px"},
                    ),
                    dbc.Label("Confidence", className="mt-2"),
                    dcc.Slider(
                        id="bulk-confidence",
                        min=1,
                        max=10,
                        step=1,
                        value=8,
                        marks={1: "1", 5: "5", 10: "10"},
                        tooltip={"placement": "bottom", "always_visible": False},
                    ),
                    dbc.Button(
                        "Apply to selected",
                        id="bulk-apply",
                        color="primary",
                        className="mt-3",
                    ),
                    dbc.Button(
                        "↻ Refresh",
                        id="sources-refresh",
                        color="secondary",
                        outline=True,
                        size="sm",
                        className="mt-2",
                    ),
                    html.Div(id="bulk-feedback", className="mt-3"),

                    html.Hr(),
                    html.H6("Bulk tag", className="fw-bold"),
                    dbc.Select(
                        id="sources-tag-select",
                        options=_initial_tag_select_options(),
                        value="",
                        className="mb-2",
                    ),
                    dbc.RadioItems(
                        id="sources-tag-action",
                        options=_TAG_ACTIONS,
                        value="add",
                        inline=True,
                        className="mb-2 small",
                    ),
                    dbc.Button(
                        "Apply to selected",
                        id="sources-tag-apply",
                        color="primary",
                        outline=True,
                    ),
                    html.Div(id="sources-tag-feedback", className="mt-2"),

                    html.Hr(),
                    html.H6("More bulk actions", className="fw-bold"),
                    dbc.Select(
                        id="bulk-more-action",
                        options=[
                            {"label": "Mark as duplicate", "value": "duplicate"},
                            {"label": "Move to abstract screening", "value": "to_screening"},
                            {"label": "Move back to full-text", "value": "to_fulltext"},
                        ],
                        value="duplicate",
                        className="mb-2",
                    ),
                    dbc.Button("Apply to selected", id="bulk-more-apply", color="primary", outline=True),
                ],
                width=3,
            ),
            dbc.Col(
                [
                    dbc.Row(
                        [
                            dbc.Col(
                                [dbc.Label("View", className="small fw-bold mb-0"),
                                 dbc.Select(id="sources-col-preset", options=_PRESET_OPTIONS, value="all", size="sm")],
                                width=3,
                            ),
                            dbc.Col(
                                [dbc.Label("Columns", className="small fw-bold mb-0"),
                                 dcc.Dropdown(id="sources-col-select", options=_COLUMN_OPTIONS, value=_PRESETS["all"], multi=True, clearable=False)],
                                width=9,
                            ),
                        ],
                        className="g-2 mb-2 align-items-end",
                    ),
                    dbc.Input(
                        id="sources-quickfilter",
                        placeholder="Quick filter — type to search all columns (or use each column's filter menu)",
                        debounce=False,
                        className="mb-2",
                    ),
                    dbc.Button(
                        "View AI extraction of selected row",
                        id="sources-view-extract",
                        color="secondary",
                        outline=True,
                        size="sm",
                        className="mb-2",
                    ),
                    dag.AgGrid(
                        id="sources-grid",
                        columnDefs=_build_column_defs(_PRESETS["all"]),
                        rowData=_initial_overview_rows(),
                        defaultColDef={
                            "resizable": True,
                            "sortable": True,
                            "filter": True,
                        },
                        dashGridOptions=dict(_GRID_OPTIONS),
                        columnSize="sizeToFit",
                        style={"height": None},
                    ),
                ],
                width=9,
            ),
        ]
    )])


def register_callbacks(app: Any) -> None:
    @app.callback(
        Output("sources-grid", "dashGridOptions"),
        Input("sources-quickfilter", "value"),
        prevent_initial_call=True,
    )
    def _quick_filter(text):
        return {**_GRID_OPTIONS, "quickFilterText": text or ""}

    @app.callback(
        Output("sources-col-select", "value"),
        Input("sources-col-preset", "value"),
        prevent_initial_call=True,
    )
    def _apply_preset(preset):
        return _PRESETS.get(preset, _PRESETS["screening"])

    @app.callback(
        Output("sources-extract-modal", "is_open"),
        Output("sources-extract-title", "children"),
        Output("sources-extract-body", "children"),
        Input("sources-view-extract", "n_clicks"),
        State("sources-grid", "selectedRows"),
        prevent_initial_call=True,
    )
    def _view_extraction(_n, selected):
        if not selected:
            return True, "AI extraction", dbc.Alert("Select a row first (checkbox in the ID column).", color="warning", className="mb-0")
        sid = selected[0].get("id")
        if sid is None:
            return no_update, no_update, no_update
        return True, f"AI extraction — #{sid}", _extraction_detail(int(sid))

    @app.callback(
        Output("sources-grid", "rowData"),
        Input("sources-refresh", "n_clicks"),
        Input("bulk-feedback", "children"),
        Input("sources-tag-feedback", "children"),
        Input("tags-refresh", "data"),
        Input("sources-filter-stage", "value"),
        Input("sources-filter-decision", "value"),
        prevent_initial_call=True,
    )
    def _populate(_refresh, _feedback, _tag_feedback, _tag_refresh, stage_field, decision):
        rows = _initial_overview_rows()
        if decision and stage_field:
            if decision == "__none__":
                rows = [r for r in rows if not r.get(stage_field)]
            else:
                rows = [r for r in rows if r.get(stage_field) == decision]
        return rows

    @app.callback(
        Output("sources-grid", "columnDefs"),
        Input("sources-col-select", "value"),
    )
    def _set_columns(selected):
        return _build_column_defs(selected)

    @app.callback(
        Output("sources-tag-select", "options"),
        Input("tags-refresh", "data"),
        prevent_initial_call=True,
    )
    def _populate_tag_select(_r):
        return _initial_tag_select_options()

    @app.callback(
        Output("sources-tag-feedback", "children"),
        Input("sources-tag-apply", "n_clicks"),
        State("sources-grid", "selectedRows"),
        State("sources-tag-select", "value"),
        State("sources-tag-action", "value"),
        prevent_initial_call=True,
    )
    def _apply_tag(_clicks, selected, tag_value, action):
        if not tag_value:
            return dbc.Alert("Pick a tag first.", color="warning")
        if not selected:
            return dbc.Alert("No rows selected.", color="warning")
        try:
            tag_id = int(tag_value)
        except (TypeError, ValueError):
            return dbc.Alert("Invalid tag.", color="warning")
        db = get_project().db
        count = 0
        for row in selected:
            sid = row.get("id")
            if sid is None:
                continue
            if action == "remove":
                db.untag_source(int(sid), tag_id)
            else:
                db.tag_source(int(sid), tag_id)
            count += 1
        verb = "Removed tag from" if action == "remove" else "Tagged"
        return dbc.Alert(f"{verb} {count} source(s).", color="success")

    @app.callback(
        Output("bulk-feedback", "children"),
        Input("bulk-apply", "n_clicks"),
        State("sources-grid", "selectedRows"),
        State("bulk-stage", "value"),
        State("bulk-decision", "value"),
        State("bulk-reasoning", "value"),
        State("bulk-confidence", "value"),
        State("shared-reviewer", "value"),
        prevent_initial_call=True,
    )
    def _bulk_apply(_clicks, selected, stage, decision, reasoning, confidence, reviewer):
        rid = (reviewer or "").strip()
        if not rid:
            return dbc.Alert("Set your reviewer ID at the top first.", color="warning")
        if not selected:
            return dbc.Alert("No rows selected. Use the checkbox in the leftmost column.", color="warning")

        project = get_project()
        db = project.db
        stage = stage if stage in ("abstract", "full_text") else "abstract"
        reason_text = (reasoning or "").strip() or "(bulk action)"
        conf = float(confidence) if confidence else None

        count = 0
        for row in selected:
            sid = row.get("id")
            if sid is None:
                continue
            db.insert_screening_decision(
                ScreeningDecision(
                    decision=decision,
                    reasoning=reason_text,
                    reviewer_type="human",
                    reviewer_id=rid,
                    source_id=int(sid),
                    stage=stage,
                    confidence=conf,
                )
            )
            count += 1
        stage_label = "abstract" if stage == "abstract" else "full-text"
        return dbc.Alert(
            f"Marked {count} source(s) as {decision} at the {stage_label} stage (by {rid}).",
            color="success",
        )

    @app.callback(
        Output("bulk-feedback", "children", allow_duplicate=True),
        Input("bulk-more-apply", "n_clicks"),
        State("sources-grid", "selectedRows"),
        State("bulk-more-action", "value"),
        State("shared-reviewer", "value"),
        prevent_initial_call=True,
    )
    def _bulk_more(_clicks, selected, action, reviewer):
        if not selected:
            return dbc.Alert("No rows selected. Use the checkbox in the leftmost column.", color="warning")
        rid = (reviewer or "").strip() or "?"
        db = get_project().db
        count = 0
        for row in selected:
            sid = row.get("id")
            if sid is None:
                continue
            sid = int(sid)
            if action == "duplicate":
                db.mark_source_duplicate(sid, True)
            elif action == "to_screening":
                db.delete_all_screening_decisions(sid, reviewer_type="human")
                db.delete_reconciliations_for_source(sid)
                db.insert_screening_action(sid, rid, action="move_to_screening")
            elif action == "to_fulltext":
                db.delete_stage_decisions(sid, "full_text", reviewer_type="human")
                db.delete_reconciliations_for_source(sid, "full_text_screening")
                db.insert_screening_action(sid, rid, action="move_to_full_text")
            else:
                continue
            count += 1
        labels = {
            "duplicate": "marked as duplicate",
            "to_screening": "moved to abstract screening",
            "to_fulltext": "moved back to full-text",
        }
        return dbc.Alert(f"{count} source(s) {labels.get(action, action)}.", color="success")
