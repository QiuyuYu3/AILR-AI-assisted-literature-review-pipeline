"""Extraction review tab: schema-driven form, ag-grid for list-of-objects."""

import json
import time
from pathlib import Path
from typing import Any

import dash_ag_grid as dag
import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update

from ailr.core.config import save_stage_workflow
from ailr.core.source import Source
from ailr.ui import ai_runner
from ailr.extraction import FieldSpec, compose_schema
from ailr.reviewers import ExtractionResult
from ailr.ui._common import format_authors, get_project, reload_project

_WORKFLOW_OPTIONS = [
    {"label": "verify (AI extracts, you verify)", "value": "verify"},
    {"label": "independent (you extract blind)", "value": "independent"},
]

_INDEP_TEAM_SIZE = 2  # independent extraction: reveal/compare once this many humans have submitted

_APP_CHROME_PX = 115  # height of the app header + tab bar above this view; the panes flex-fill the rest


def extraction_workflow_block() -> list[Any]:
    """Extraction workflow setting. Rendered on the full-text Workflow tab."""
    project = get_project()
    return [
        dbc.Label("Extraction workflow", className="fw-bold"),
        dbc.Select(id="extract-workflow", options=_WORKFLOW_OPTIONS, value=project.config.extraction.workflow, size="sm"),
        html.Ul(
            [
                html.Li([html.Strong("verify: "), "AI extracts, human verifies/edits."], className="small"),
                html.Li([html.Strong("independent: "), "human extracts blind, AI hidden until submit."], className="small"),
            ],
            className="mt-1",
        ),
        html.Div(id="extract-workflow-saved", className="text-success small"),
    ]


def ai_extraction_panel() -> list[Any]:
    """Run AI extraction + import externally-run results. Rendered on the AI extraction tab."""
    return [
        dbc.Label("AI extraction", className="fw-bold"),
        dbc.Switch(id="extract-ai-mock", label="Mock (no API cost)", value=True, className="small"),
        dbc.Switch(id="extract-ai-force", label="Force re-extract (overwrite existing AI data)", value=False, className="small"),
        html.P("Runs on papers that passed abstract screening (include) and have full-text markdown. Already-extracted papers are skipped unless 'Force re-extract' is on.", className="text-muted small mb-1"),
        dbc.Button("Run AI extraction", id="extract-ai-run", color="primary", outline=True, size="sm"),
        html.Div(id="extract-ai-status", className="small mt-2"),
        dcc.Interval(id="extract-ai-poll", interval=1200, disabled=True),
        dcc.ConfirmDialogProvider(
            dbc.Button("Clear mock AI results", color="link", size="sm", className="text-danger p-0 mt-2"),
            id="extract-clear-mock",
            message="Delete all MOCK AI extractions in this project (including the derived full-text verdicts)? Real AI and human extractions are kept.",
        ),
        html.Div(id="extract-clear-mock-status", className="small mt-1"),
        html.Details(
            [
                html.Summary("Import AI extraction results (ran it yourself)", className="small"),
                html.P("Path to a results .json (list / one record) or a FOLDER of per-paper .json. Keys are fixed reserved names. Generate a matching template/prompt on the Template tab.", className="text-muted small mb-1"),
                html.Ul(
                    [
                        html.Li([html.Code("source_id"), " or ", html.Code("doi"), " — which paper (else the filename is used)."], className="small"),
                        html.Li([html.Code("extraction"), " — required object; ", html.Strong("each key = a field name"), " (match your Template), value is the value or ", html.Code('{"value":…, "quote":…}'), "."], className="small"),
                        html.Li([html.Code("flag_check.decision"), " — optional full-text decision (", html.Code("include / exclude / uncertain"), ")."], className="small"),
                    ],
                    className="mb-1",
                ),
                dbc.InputGroup(
                    [
                        dbc.Input(id="extract-importai-path", placeholder="C:/path/to/results.json or folder", size="sm"),
                        dbc.Button("Import", id="extract-importai-run", color="secondary", outline=True, size="sm"),
                    ],
                    className="mt-2",
                ),
                html.Div(id="extract-importai-status", className="small mt-1"),
            ],
            className="small mt-2",
        ),
    ]


def layout() -> Any:
    # Full-height two-pane (Covidence-style): flexbox makes the panes grow to fill the window, so the
    # reader and form each scroll independently and resize with the window — no per-element vh guesses.
    # The only fixed bit is the app chrome ABOVE this view (header + tabs ≈ 115px); tweak _APP_CHROME_PX
    # below if the bottom shows a gap or an extra page scrollbar.
    scroll_pane = {"flex": "1 1 0", "minWidth": 0, "minHeight": 0, "overflowY": "auto", "paddingRight": "6px"}
    return html.Div(
        [
            # ── Top bar: Back · current paper title · Save/Submit. One paper at a time, opened from
            #    the full-text list — no positional Prev/Next, so re-mounts can never mis-navigate. ──
            dbc.Row(
                [
                    dbc.Col(dbc.Button("← Back to full-text review", id="extract-back", color="link", size="sm", className="p-0"), width="auto"),
                    dbc.Col(
                        dbc.RadioItems(
                            id="extract-reader-mode",
                            options=[{"label": "PDF", "value": "pdf"}, {"label": "Markdown", "value": "md"}],
                            value="pdf", inline=True,
                        ),
                        width="auto",
                    ),
                    dbc.Col(
                        [
                            dbc.Button("Save draft", id="extract-save", color="secondary", outline=True, size="sm", className="me-2"),
                            dbc.Button("Submit extraction", id="extract-submit", color="primary", size="sm"),
                        ],
                        width="auto",
                        className="ms-auto",
                    ),
                ],
                className="align-items-center g-2 mb-1",
            ),
            html.Div(id="extract-feedback", className="small text-success mb-1"),
            html.Hr(className="my-1"),

            # ── Two independently-scrolling panes that flex-fill the remaining height ──
            html.Div(
                [
                    # Left: reader (PDF/Markdown) fills its column
                    html.Div(
                        html.Div(id="extract-reader", style={"flex": "1 1 0", "minHeight": 0, "display": "flex", "flexDirection": "column"}),
                        style={"flex": "1 1 0", "minWidth": 0, "display": "flex", "flexDirection": "column", "paddingRight": "8px"},
                    ),
                    # Right: source card + AI panel + the editable form, scrolling on its own
                    html.Div(
                        [
                            html.Div(id="extract-source-card"),
                            # Source-level actions live in the static layout (not inside the
                            # dynamically-rendered card) so their callback Inputs always resolve
                            # — otherwise dash-renderer hard-errors (white screen) when no source.
                            html.Div(
                                [
                                    dbc.Button("Duplicate", id="extract-duplicate", size="sm", color="link", className="p-0 me-3 text-danger"),
                                    dbc.Button("↺ Move to screening", id="extract-move-screen", size="sm", color="link", className="p-0 me-3 text-secondary"),
                                    dbc.Button("↺ Move back to full-text", id="extract-move-ft", size="sm", color="link", className="p-0 text-secondary"),
                                ],
                                className="mt-1 mb-1",
                            ),
                            html.Div(id="extract-ai-panel"),
                            html.Hr(),
                            html.H6("Your extraction", className="d-inline me-2"),
                            html.Small("(edit AI's values; AI original shown under each field)", className="text-muted"),
                            html.Div(id="extract-form-container", className="mt-2"),
                            html.Small("Save draft keeps your edits without finalizing — click it before leaving, edits aren't kept otherwise. Submit marks the paper done and returns you to the list.", className="text-muted d-block mt-3"),
                        ],
                        style=scroll_pane,
                    ),
                ],
                style={"display": "flex", "flex": "1 1 auto", "minHeight": 0, "gap": "8px"},
            ),
        ],
        style={"display": "flex", "flexDirection": "column", "height": f"calc(100vh - {_APP_CHROME_PX}px)"},
    )


def register_callbacks(app: Any) -> None:
    @app.callback(
        Output("tabs", "data", allow_duplicate=True),
        Input("extract-back", "n_clicks"),
        prevent_initial_call=True,
    )
    def _back_to_ft(n):
        return "full_text" if n else no_update

    @app.callback(
        Output("extract-workflow-saved", "children"),
        Input("extract-workflow", "value"),
        prevent_initial_call=True,
    )
    def _save_workflow(value):
        if not value:
            return no_update
        project = get_project()
        if value == project.config.extraction.workflow:
            return no_update
        save_stage_workflow(project.root, "extraction", value)
        reload_project()
        return f"saved: extraction.workflow = {value}"

    @app.callback(
        Output("extract-importai-status", "children"),
        Output("extract-refresh", "data", allow_duplicate=True),
        Input("extract-importai-run", "n_clicks"),
        State("extract-importai-path", "value"),
        prevent_initial_call=True,
    )
    def _import_ai_results(n, path):
        if not n:
            return no_update, no_update
        p = Path((path or "").strip())
        if not path or not p.exists():
            return dbc.Alert("Enter a valid file or folder path.", color="warning", className="py-1 mb-0"), no_update
        files = [p] if p.is_file() else sorted(p.glob("*.json"))
        if not files:
            return dbc.Alert("No .json file(s) found.", color="warning", className="py-1 mb-0"), no_update

        records: list = []
        errors: list[str] = []
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:
                errors.append(f"{f.name}: {e}")
                continue
            recs = data if isinstance(data, list) else [data]
            for rec in recs:
                if isinstance(rec, dict) and rec.get("source_id") is None and not rec.get("doi"):
                    if f.stem.isdigit():
                        rec["source_id"] = int(f.stem)
                    else:
                        rec["doi"] = f.stem.replace("_", "/")
                records.append(rec)

        from ailr.ingest.results_import import import_ai_results

        s = import_ai_results(get_project(), records)
        msg = f"Imported {s.imported}/{s.total_records} record(s); {s.fields_written} fields, {s.flags_written} flags; {len(s.unmatched)} unmatched."
        if errors:
            msg += f" {len(errors)} file error(s)."
        return dbc.Alert(msg, color="success", className="py-1 mb-0"), {"ts": time.time()}

    @app.callback(
        Output("extract-reader", "children"),
        Input("extract-store", "data"),
        Input("extract-reader-mode", "value"),
    )
    def _render_reader_pane(store, mode):
        sid = (store or {}).get("sid")
        if not sid:
            return html.Small("Open a paper from the Full-text review page.", className="text-muted")
        proj = get_project()
        src = proj.db.get_source(int(sid))
        if src is None:
            return ""
        # height:100% fills the flex pane the reader sits in (see layout()).
        if mode == "pdf":
            if not src.pdf_path:
                return dbc.Alert("No PDF linked for this source.", color="warning")
            return html.Iframe(src=f"/pdf/{sid}", style={"width": "100%", "height": "100%", "border": "none"})
        md_text = None
        if src.markdown_path:
            p = Path(src.markdown_path)
            if not p.is_absolute():
                p = proj.root / p
            if p.exists():
                md_text = p.read_text(encoding="utf-8")
        if not md_text:
            return dbc.Alert("No markdown yet for this source.", color="info")
        return dcc.Markdown(md_text, style={"height": "100%", "overflow": "auto"})

    @app.callback(
        Output("extract-ai-poll", "disabled"),
        Output("extract-ai-status", "children"),
        Input("extract-ai-run", "n_clicks"),
        State("extract-ai-mock", "value"),
        State("extract-ai-force", "value"),
        prevent_initial_call=True,
    )
    def _ai_run(n, mock, force):
        if not n:
            return no_update, no_update
        started = ai_runner.start_extraction(get_project(), bool(mock), force=bool(force))
        msg = "AI extraction started…" if started else "Already running…"
        return False, dbc.Alert(msg, color="info", className="py-1 mb-0")

    @app.callback(
        Output("extract-clear-mock-status", "children"),
        Output("extract-refresh", "data", allow_duplicate=True),
        Input("extract-clear-mock", "submit_n_clicks"),
        prevent_initial_call=True,
    )
    def _clear_mock(n):
        if not n:
            return no_update, no_update
        import time as _t
        project = get_project()
        cleared = project.db.clear_mock_ai_extractions(project.project_id)
        return dbc.Alert(f"Cleared {cleared} mock AI extraction row(s).", color="success", className="py-1 mb-0"), {"ts": _t.time()}

    @app.callback(
        Output("extract-ai-status", "children", allow_duplicate=True),
        Output("extract-ai-poll", "disabled", allow_duplicate=True),
        Output("extract-refresh", "data"),
        Input("extract-ai-poll", "n_intervals"),
        prevent_initial_call=True,
    )
    def _ai_poll(_n):
        st = ai_runner.get_status("extraction")
        if st.get("running"):
            done, total = st.get("done", 0), st.get("total", 0)
            pct = int(done / total * 100) if total else 0
            bar = dbc.Progress(value=pct, label=f"{done}/{total}", striped=True, animated=True, className="mt-1")
            return html.Div(["Running AI extraction…", bar]), False, no_update
        if st.get("error"):
            return dbc.Alert(f"AI extraction failed: {st['error']}", color="danger", className="py-1 mb-0"), True, no_update
        if st.get("started") and st.get("summary"):
            return dbc.Alert(st["summary"], color="success", className="py-1 mb-0"), True, {"ts": time.time()}
        return no_update, True, no_update

    # Render the currently-open paper. Driven purely by the stored source id (+ reviewer / AI-refresh)
    # — there is no positional index and no "which button fired" logic, so a page re-mount can't
    # mis-navigate. The action buttons live in a SEPARATE callback (_actions) below.
    @app.callback(
        Output("extract-source-card", "children"),
        Output("extract-form-container", "children"),
        Output("extract-ai-panel", "children"),
        Output("extract-submit", "disabled"),
        Output("extract-save", "disabled"),
        Output("extract-feedback", "children"),
        Input("extract-store", "data"),
        Input("shared-reviewer", "value"),
        Input("extract-refresh", "data"),
    )
    def _render(store, reviewer, _refresh):
        project = get_project()
        db = project.db
        rid = (reviewer or "").strip()
        sid = (store or {}).get("sid")

        if not sid:
            hint = html.Div("Open a paper from the Full-text review page to extract it.", className="text-muted")
            return "", hint, "", True, True, ""
        src = db.get_source(int(sid))
        if src is None:
            return "", html.Div("That paper is no longer available.", className="text-muted"), "", True, True, ""

        if not rid:
            return (_source_card(project.root, src),
                    dbc.Alert("Enter your reviewer ID above to begin.", color="info"),
                    "", True, True, "")

        fields = compose_schema(project.root / project.config.extraction.schema_path)
        workflow = project.config.extraction.workflow

        # Read-only: another reviewer has claimed it (verify), or all required reviewers have
        # submitted (independent). Show each relevant reviewer's table; Save/Submit off.
        locked, display_ids = _compute_locked(db, src, rid, workflow)
        if locked:
            return (_source_card(project.root, src),
                    _readonly_tables(db, src, display_ids, [f for f in fields if f.verify]),
                    _ai_panel(db, src, workflow, rid),
                    True, True, "")

        ai_data: dict[str, Any] | None = None
        if workflow == "verify":
            # Scalar fields keep their quote in the separate source_quote column; wrap them as
            # {value, quote} so the form can show it. Object/list values stay raw (quotes are nested).
            ai_data = {}
            for r in db.list_extractions(src.id, extractor_type="ai"):
                v = r["value"]
                ai_data[r["field_name"]] = v if isinstance(v, (dict, list)) else {"value": v, "quote": r.get("source_quote")}
        # Editable fields prefill from THIS reviewer's saved values (overriding AI); the AI value
        # stays visible as the "AI proposed" reference. Latest row wins (ORDER BY id).
        human_data = {
            r["field_name"]: r["value"]
            for r in db.list_extractions(src.id, extractor_type="human")
            if r.get("extractor_id") == rid
        }
        ai_values = {k: (v["value"] if isinstance(v, dict) and "value" in v else v) for k, v in (ai_data or {}).items()}
        prefill_data = {**ai_values, **human_data}

        return (_source_card(project.root, src),
                _build_form([f for f in fields if f.verify], prefill_data=prefill_data, ai_data=ai_data),
                _ai_panel(db, src, workflow, rid),
                False, False, "")

    # Buttons that act on the open paper. Submit / move / duplicate finish the paper and return to the
    # full-text list; Save persists a draft and stays put. Each is gated on its own n_clicks so a
    # fresh-mount callback fire can't trigger an action.
    @app.callback(
        Output("extract-store", "data", allow_duplicate=True),
        Output("extract-feedback", "children", allow_duplicate=True),
        Output("tabs", "data", allow_duplicate=True),
        Input("extract-submit", "n_clicks"),
        Input("extract-save", "n_clicks"),
        Input("extract-move-ft", "n_clicks"),
        Input("extract-move-screen", "n_clicks"),
        Input("extract-duplicate", "n_clicks"),
        State("extract-store", "data"),
        State("shared-reviewer", "value"),
        State({"type": "ex-value", "field": ALL}, "value"),
        State({"type": "ex-value", "field": ALL}, "id"),
        State({"type": "ex-quote", "field": ALL}, "value"),
        State({"type": "ex-quote", "field": ALL}, "id"),
        State({"type": "ex-grid", "field": ALL}, "rowData"),
        State({"type": "ex-grid", "field": ALL}, "id"),
        prevent_initial_call=True,
    )
    def _actions(submit, save, move_ft, move_screen, dup, store, reviewer,
                 val_values, val_ids, quote_values, quote_ids, grid_rows, grid_ids):
        trigger = ctx.triggered_id
        rid = (reviewer or "").strip()
        sid = (store or {}).get("sid")
        if not rid or not sid:
            return no_update, no_update, no_update
        project = get_project()
        db = project.db
        src = db.get_source(int(sid))
        if src is None:
            return no_update, no_update, no_update
        fields = compose_schema(project.root / project.config.extraction.schema_path)
        workflow = project.config.extraction.workflow

        if trigger == "extract-save" and save:
            locked, _ = _compute_locked(db, src, rid, workflow)
            if locked:
                return no_update, no_update, no_update
            _save_extraction(db, src, rid, fields, val_values, val_ids, quote_values, quote_ids, grid_rows, grid_ids, include_autoaccept=False)
            return no_update, "Draft saved.", no_update

        if trigger == "extract-submit" and submit:
            locked, _ = _compute_locked(db, src, rid, workflow)
            if locked:
                return no_update, no_update, no_update
            ai_rows = {r["field_name"]: r for r in db.list_extractions(src.id, extractor_type="ai")}
            _save_extraction(db, src, rid, fields, val_values, val_ids, quote_values, quote_ids, grid_rows, grid_ids, ai_rows=ai_rows, include_autoaccept=True)
            db.mark_extraction_submitted(src.id, rid)
            return no_update, no_update, "full_text"

        if trigger == "extract-move-ft" and move_ft:
            db.delete_stage_decisions(src.id, "full_text", reviewer_type="human")
            db.delete_reconciliations_for_source(src.id, "full_text_screening")
            db.insert_screening_action(src.id, rid, action="move_to_full_text")
            return {"sid": None}, no_update, "full_text"

        if trigger == "extract-move-screen" and move_screen:
            db.delete_all_screening_decisions(src.id, reviewer_type="human")
            db.delete_reconciliations_for_source(src.id)
            db.insert_screening_action(src.id, rid or "?", action="move_to_screening")
            return {"sid": None}, no_update, "full_text"

        if trigger == "extract-duplicate" and dup:
            db.mark_source_duplicate(src.id, True)
            return {"sid": None}, no_update, "full_text"

        return no_update, no_update, no_update

    @app.callback(
        Output({"type": "ex-grid", "field": ALL}, "rowData"),
        Input({"type": "ex-addrow", "field": ALL}, "n_clicks"),
        State({"type": "ex-grid", "field": ALL}, "rowData"),
        State({"type": "ex-grid", "field": ALL}, "id"),
        prevent_initial_call=True,
    )
    def _add_grid_row(add_clicks, all_rows, grid_ids):
        triggered = ctx.triggered_id
        out = []
        for rows, gid in zip(all_rows, grid_ids):
            rows = list(rows or [])
            if triggered and gid["field"] == triggered["field"]:
                rows.append({})
            out.append(rows)
        return out


def _source_card(root: Path, src: Source) -> Any:
    meta = []
    if src.year:
        meta.append(str(src.year))
    if src.journal:
        meta.append(src.journal)
    if src.doi:
        meta.append(f"DOI: {src.doi}")
    authors = format_authors(src.authors) if src.authors else ""
    return dbc.Card(
        dbc.CardBody(
            [
                html.H6(f"#{src.id}  {src.title}", className="mb-1"),
                html.P(" • ".join(meta), className="text-muted small mb-1"),
                html.P(authors, className="text-muted small mb-1") if authors else None,
                html.Small("Bibliographic fields above come from the imported record (not AI-extracted).", className="text-muted fst-italic"),
                html.Div(
                    [
                        dbc.Button("History", id={"type": "extract-history-btn", "source": src.id}, size="sm", color="link", className="p-0 me-3"),
                        dbc.Button("Tags", id={"type": "extract-tag-btn", "source": src.id}, size="sm", color="link", className="p-0 me-3"),
                        dbc.Button("Note", id={"type": "extract-note-btn", "source": src.id}, size="sm", color="link", className="p-0 me-3"),
                    ],
                    className="mt-1",
                ),
            ],
            className="py-2",
        )
    )


def _build_form(fields: list[FieldSpec], prefill_data: dict[str, Any] | None = None, ai_data: dict[str, Any] | None = None) -> list[Any]:
    return [_field_block(f, prefill_data, ai_data) for f in fields]


def _field_block(field: FieldSpec, prefill_data: dict[str, Any] | None, ai_data: dict[str, Any] | None) -> Any:
    if field.type == "object":
        pre_sub = prefill_data.get(field.name) if prefill_data else None
        if not isinstance(pre_sub, dict):
            pre_sub = None
        ai_sub = ai_data.get(field.name) if ai_data else None
        if not isinstance(ai_sub, dict):
            ai_sub = None
        inner = [
            _leaf_widget(
                sub,
                dotted=f"{field.name}.{sub.name}",
                prefill_cell=(pre_sub or {}).get(sub.name),
                ai_cell=(ai_sub or {}).get(sub.name),
            )
            for sub in field.fields or []
        ]
        return dbc.Card(
            dbc.CardBody([html.H6(field.name), _desc(field)] + inner),
            className="mb-2",
        )

    if field.type == "list" and field.item_type == "object":
        item_fields = field.item_fields or []
        column_defs = [{"field": s.name, "editable": True, "tooltipField": s.name} for s in item_fields]
        prefill_rows: list[dict] = []
        src_list = prefill_data.get(field.name) if prefill_data else None
        if isinstance(src_list, list):
            prefill_rows = [_flatten_list_item(item, item_fields) for item in src_list]
        return dbc.Card(
            dbc.CardBody(
                [
                    html.H6(f"{field.name} (list of objects)"),
                    _desc(field),
                    dag.AgGrid(
                        id={"type": "ex-grid", "field": field.name},
                        columnDefs=column_defs,
                        rowData=prefill_rows,
                        # wrapText + autoHeight so long values show in full (rows grow); minWidth keeps
                        # each column readable, so many-column fields scroll sideways instead of cramming.
                        defaultColDef={"editable": True, "resizable": True, "sortable": True, "wrapText": True, "autoHeight": True, "minWidth": 150},
                        dashGridOptions={"rowSelection": {"mode": "multiRow"}, "domLayout": "autoHeight"},
                        columnSize="sizeToFit",
                        style={"height": None},
                    ),
                    dbc.Button(
                        "+ Add row",
                        id={"type": "ex-addrow", "field": field.name},
                        size="sm",
                        color="secondary",
                        outline=True,
                        className="mt-2",
                    ),
                    _ai_grid_reference(ai_data.get(field.name) if ai_data else None, item_fields),
                ]
            ),
            className="mb-2",
        )

    if field.type == "list":
        prefill_text = ""
        src_list = prefill_data.get(field.name) if prefill_data else None
        if isinstance(src_list, list):
            prefill_text = "\n".join(str(v) for v in src_list)
        return dbc.Card(
            dbc.CardBody(
                [
                    html.H6(f"{field.name} (list of {field.item_type})"),
                    _desc(field),
                    dbc.Textarea(
                        id={"type": "ex-value", "field": field.name},
                        placeholder="one item per line",
                        style={"height": "80px"},
                        value=prefill_text,
                    ),
                    _ai_list_reference(ai_data.get(field.name) if ai_data else None),
                ]
            ),
            className="mb-2",
        )

    return dbc.Card(
        dbc.CardBody(
            _leaf_widget(
                field,
                dotted=field.name,
                prefill_cell=prefill_data.get(field.name) if prefill_data else None,
                ai_cell=ai_data.get(field.name) if ai_data else None,
            )
        ),
        className="mb-2",
    )


def _number_like(v: Any) -> bool:
    """Whether v can sit in a type='number' input (numbers, numeric strings, or empty)."""
    if v is None or v == "":
        return True
    if isinstance(v, bool):
        return False
    try:
        float(v)
        return True
    except (ValueError, TypeError):
        return False


def _unwrap_cell(cell: Any) -> tuple[Any, Any]:
    """Extract (value, quote) from an AI cell that may be wrapped as {value, quote} or raw."""
    if isinstance(cell, dict) and "value" in cell:
        return cell.get("value"), cell.get("quote")
    return cell, None


def _flatten_list_item(item: Any, item_fields: list[FieldSpec]) -> dict:
    """For ag-grid prefill: AI may return [{name: {value:..,quote:..}, ...}]; flatten to {name: value}."""
    if not isinstance(item, dict):
        return {}
    out: dict = {}
    for f in item_fields:
        cell = item.get(f.name)
        v, _q = _unwrap_cell(cell)
        out[f.name] = v
    return out


def _ai_list_reference(ai_val: Any) -> Any:
    """Read-only 'AI proposed' line for a list-of-string field (matches the leaf widget's reference)."""
    if not isinstance(ai_val, list) or not ai_val:
        return None
    items = [str(_unwrap_cell(v)[0]) for v in ai_val if _unwrap_cell(v)[0] not in (None, "")]
    if not items:
        return None
    return html.Small("AI proposed: " + "; ".join(items), className="text-muted d-block fst-italic mt-1")


def _ai_grid_reference(ai_val: Any, item_fields: list[FieldSpec]) -> Any:
    """Collapsible read-only 'AI proposed' table for a list-of-object field, so you can compare your
    edits against AI's original rows (the editable grid above is pre-filled but loses the reference)."""
    if not isinstance(ai_val, list) or not ai_val:
        return None
    rows = [_flatten_list_item(it, item_fields) for it in ai_val]
    head = html.Thead(html.Tr([html.Th(s.name) for s in item_fields]))
    body = html.Tbody([
        html.Tr([
            html.Td("" if r.get(s.name) is None else str(r.get(s.name)), style={"whiteSpace": "pre-wrap", "fontSize": "0.75rem"})
            for s in item_fields
        ])
        for r in rows
    ])
    return html.Details(
        [
            html.Summary(html.Small("AI proposed (reference)", className="text-muted")),
            dbc.Table([head, body], bordered=True, size="sm", className="mt-1", style={"fontSize": "0.75rem"}),
        ],
        className="mt-1",
    )


def _leaf_widget(field: FieldSpec, dotted: str, prefill_cell: Any = None, ai_cell: Any = None) -> Any:
    label = html.Strong(field.name + (" *" if field.required else ""))
    ai_value, ai_quote = _unwrap_cell(ai_cell)
    # Editable widget shows the effective value (the human's saved value if any, else AI).
    pre_value, pre_quote = _unwrap_cell(prefill_cell)

    common: dict = {}
    if pre_value is not None:
        common["value"] = pre_value

    if field.type == "string" and field.enum:
        value_widget = dbc.Select(
            id={"type": "ex-value", "field": dotted},
            options=[{"label": e, "value": e} for e in field.enum],
            **common,
        )
    elif field.type in ("integer", "number"):
        # A number input can't hold a non-numeric value (e.g. AI wrote "NR"); the browser logs
        # "value cannot be parsed". Fall back to a text box so the value still shows and stays editable.
        if _number_like(pre_value):
            value_widget = dbc.Input(
                id={"type": "ex-value", "field": dotted},
                type="number",
                step=1 if field.type == "integer" else "any",
                **common,
            )
        else:
            value_widget = dbc.Input(id={"type": "ex-value", "field": dotted}, type="text", **common)
    elif field.type == "boolean":
        value_widget = dbc.Checkbox(
            id={"type": "ex-value", "field": dotted},
            value=bool(pre_value) if pre_value is not None else False,
        )
    else:
        value_widget = dbc.Textarea(
            id={"type": "ex-value", "field": dotted},
            style={"height": "60px"},
            **common,
        )

    differs = (
        ai_value is not None
        and pre_value is not None
        and str(pre_value).strip() != str(ai_value).strip()
    )

    children: list[Any] = [label, _desc(field), value_widget]
    if ai_value is not None:
        children.append(
            html.Small(
                [
                    dbc.Badge("changed from AI", color="warning", className="me-2") if differs else None,
                    f"AI proposed: {ai_value}",
                ],
                className="text-muted d-block",
                style={"fontStyle": "italic"},
            )
        )
    if ai_quote:
        children.append(
            html.Small(f"AI quote: “{ai_quote}”", className="text-muted d-block", style={"fontStyle": "italic"})
        )
    children.append(
        dbc.Textarea(
            id={"type": "ex-quote", "field": dotted},
            placeholder="verbatim quote (optional)",
            style={"height": "44px"},
            className="mt-1",
            value=pre_quote or "",
        )
    )
    block_style = {"borderLeft": "3px solid #f0ad4e", "paddingLeft": "8px"} if differs else None
    return html.Div(children, className="mb-2", style=block_style)


def _desc(field: FieldSpec) -> Any:
    if field.description:
        return html.P(field.description, className="text-muted small mb-1")
    return html.Span()


def _ai_panel(db: Any, src: Source, workflow: str, rid: str) -> Any:
    ai_rows = db.list_extractions(src.id, extractor_type="ai")
    flag_check = db.get_flag_check(src.id, extractor_type="ai")
    if not ai_rows and not flag_check:
        return html.P("No AI extraction yet.", className="text-muted small")

    # In `independent` mode, AI is hidden until the current human submits.
    # In `verify` mode, AI is always shown (and Batch 2 will pre-fill the form from it).
    if workflow == "independent" and not db.has_submitted(src.id, rid):
        return dbc.Alert("AI extraction hidden until you submit (workflow: independent).", color="secondary")

    items: list[Any] = [html.H6("AI extraction")]
    for row in ai_rows:
        block: list[Any] = [
            html.Div([html.Strong(f"{row['field_name']}: "), html.Code(json.dumps(row["value"], ensure_ascii=False)[:300])]),
        ]
        if row.get("source_quote"):
            block.append(html.Div(f"“{row['source_quote']}”", className="text-muted fst-italic ms-3"))
        meta = []
        if row.get("confidence") is not None:
            meta.append(f"confidence: {row['confidence']}")
        if row.get("page_or_section"):
            meta.append(f"@ {row['page_or_section']}")
        if meta:
            block.append(html.Div(" • ".join(meta), className="text-muted ms-3", style={"fontSize": "0.75rem"}))
        items.append(html.Div(block, className="small mb-2"))
    if flag_check:
        items.append(html.H6("flag_check", className="mt-2"))
        for fc in flag_check:
            emoji = {"PASS": "[PASS]", "FAIL": "[FAIL]", "UNCERTAIN": "[?]"}.get(fc.get("verdict", ""), "")
            items.append(
                html.P(f"{emoji} {fc.get('criterion_id')}: {fc.get('reason')}", className="small mb-1")
            )
    return dbc.Card(dbc.CardBody(items), color="light")


def _save_extraction(
    db: Any,
    src: Source,
    rid: str,
    fields: list[FieldSpec],
    val_values: list,
    val_ids: list,
    quote_values: list,
    quote_ids: list,
    grid_rows: list,
    grid_ids: list,
    ai_rows: dict[str, Any] | None = None,
    include_autoaccept: bool = True,
) -> None:
    """Persist this reviewer's extraction in ONE batched commit. include_autoaccept=False (Save
    draft) writes only the human-verified fields; True (Submit) also takes AI's values for the
    fields not flagged for verification."""
    values: dict[str, Any] = {vid["field"]: v for vid, v in zip(val_ids, val_values)}
    quotes: dict[str, Any] = {qid["field"]: q for qid, q in zip(quote_ids, quote_values)}
    grids: dict[str, Any] = {gid["field"]: r for gid, r in zip(grid_ids, grid_rows)}
    ai_rows = ai_rows or {}

    results: list[ExtractionResult] = []
    for field in fields:
        # Fields not flagged for human verification take the AI value as-is (Submit only).
        if not field.verify:
            if not include_autoaccept:
                continue
            row = ai_rows.get(field.name)
            if row is not None:
                results.append(
                    ExtractionResult(
                        extractor_type="human", extractor_id=rid,
                        field_name=field.name, value=row.get("value"),
                        source_quote=row.get("source_quote"), source_id=src.id,
                        prompt_version="ai-accepted",
                    )
                )
            continue
        if field.type == "object":
            obj: dict[str, Any] = {}
            for sub in field.fields or []:
                key = f"{field.name}.{sub.name}"
                obj[sub.name] = {"value": values.get(key), "quote": quotes.get(key)}
            results.append(
                ExtractionResult(
                    extractor_type="human", extractor_id=rid,
                    field_name=field.name, value=obj, source_id=src.id,
                    prompt_version="manual",
                )
            )
        elif field.type == "list" and field.item_type == "object":
            results.append(
                ExtractionResult(
                    extractor_type="human", extractor_id=rid,
                    field_name=field.name, value=grids.get(field.name, []),
                    source_id=src.id, prompt_version="manual",
                )
            )
        elif field.type == "list":
            raw = values.get(field.name) or ""
            items = [line.strip() for line in str(raw).splitlines() if line.strip()]
            results.append(
                ExtractionResult(
                    extractor_type="human", extractor_id=rid,
                    field_name=field.name, value=items, source_id=src.id,
                    prompt_version="manual",
                )
            )
        else:
            results.append(
                ExtractionResult(
                    extractor_type="human", extractor_id=rid,
                    field_name=field.name, value=values.get(field.name),
                    source_quote=quotes.get(field.name), source_id=src.id,
                    prompt_version="manual",
                )
            )

    db.insert_extractions(results)


def _compute_locked(db: Any, src: Source, rid: str, workflow: str) -> tuple[bool, list[str]]:
    """Whether this paper is read-only for the current reviewer, and whose extraction(s) to show.
    verify: another reviewer has claimed it (draft or submitted) → show that one reviewer.
    independent: every required reviewer has submitted → show all submitters."""
    if workflow == "verify":
        other = db.other_human_extracted(src.id, rid)
        return (other is not None), ([other] if other else [])
    if workflow == "independent":
        submitters = db.extraction_submitters(src.id)
        return (len(submitters) >= _INDEP_TEAM_SIZE), submitters
    return False, []


def _readonly_tables(db: Any, src: Source, submitter_ids: list[str], fields: list[FieldSpec]) -> Any:
    """Read-only view of each submitter's extraction (one table per reviewer). Used when the paper
    is owned by another reviewer (verify) or all reviewers have submitted (independent)."""
    if not submitter_ids:
        return html.P("No submitted extraction to show yet.", className="text-muted small")
    field_order = [f.name for f in fields]
    human_rows = db.list_extractions(src.id, extractor_type="human")
    blocks: list[Any] = []
    for ext_id in submitter_ids:
        latest: dict[str, Any] = {}
        for r in human_rows:                      # ORDER BY id → later rows win
            if r.get("extractor_id") == ext_id:
                latest[r["field_name"]] = r["value"]
        ordered = field_order + [k for k in latest if k not in field_order]
        trs = []
        for fname in ordered:
            if fname not in latest:
                continue
            v = latest[fname]
            disp = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else ("" if v is None else str(v))
            trs.append(html.Tr([html.Td(html.Strong(fname)), html.Td(disp, style={"whiteSpace": "pre-wrap"})]))
        blocks.append(
            html.Div(
                [
                    html.H6(["Extraction by ", html.Span(ext_id, className="text-primary")], className="mt-3"),
                    dbc.Table([html.Tbody(trs)], bordered=True, hover=True, size="sm", striped=True)
                    if trs else html.P("(no values)", className="text-muted small"),
                ]
            )
        )
    return html.Div(
        [dbc.Alert("Read-only — you cannot edit another reviewer's extraction.", color="info", className="py-1 mb-2")]
        + blocks
    )
