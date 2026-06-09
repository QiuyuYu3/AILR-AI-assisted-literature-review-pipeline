"""Extraction review tab: schema-driven form, ag-grid for list-of-objects."""

import json
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

_FILTERS = [
    {"label": "Full-text includes (with markdown)", "value": "includes"},
    {"label": "All with markdown", "value": "all"},
]

_WORKFLOW_OPTIONS = [
    {"label": "verify (AI extracts, you verify)", "value": "verify"},
    {"label": "independent (you extract blind)", "value": "independent"},
]


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
    return html.Div(
        [
            dbc.Row(
                [
                    dbc.Col([dbc.Label("Source set", className="small mb-0"),
                             dbc.RadioItems(id="extract-filter", options=_FILTERS, value="includes", inline=True)], width=6),
                    dbc.Col([html.Div(id="extract-progress", className="small fw-bold"),
                             dbc.ButtonGroup([
                                 dbc.Button("← Prev", id="extract-prev", color="secondary", outline=True, size="sm"),
                                 dbc.Button("Next →", id="extract-next", color="secondary", outline=True, size="sm"),
                             ])], width=6),
                ],
                className="mb-1 align-items-end",
            ),
            html.Small("Configure fields/prompt, run AI extraction, and import results on the Full text & extraction → Workflow page (Template / AI extraction tabs).", className="text-muted"),
            html.Hr(className="my-2"),

            dbc.Row(
                [
                    dbc.Col(
                        [
                            dbc.RadioItems(
                                id="extract-reader-mode",
                                options=[{"label": "PDF", "value": "pdf"}, {"label": "Markdown", "value": "md"}],
                                value="pdf", inline=True, className="mb-1",
                            ),
                            dcc.Loading(html.Div(id="extract-reader")),
                        ],
                        width=6,
                    ),
                    dbc.Col(
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
                            html.Div(dbc.Button("Submit extraction", id="extract-submit", color="primary", className="mt-3")),
                            html.Div(id="extract-feedback", className="mt-2 text-success"),
                        ],
                        width=6,
                    ),
                ]
            ),
            dcc.Store(id="extract-current-sid", data=None),
        ]
    )


def register_callbacks(app: Any) -> None:
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
        import json as _json

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
                data = _json.loads(f.read_text(encoding="utf-8"))
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
        import time as _t
        msg = f"Imported {s.imported}/{s.total_records} record(s); {s.fields_written} fields, {s.flags_written} flags; {len(s.unmatched)} unmatched."
        if errors:
            msg += f" {len(errors)} file error(s)."
        return dbc.Alert(msg, color="success", className="py-1 mb-0"), {"ts": _t.time()}

    @app.callback(
        Output("extract-reader", "children"),
        Input("extract-current-sid", "data"),
        Input("extract-reader-mode", "value"),
    )
    def _render_reader_pane(sid, mode):
        if not sid:
            return html.Small("Select a source.", className="text-muted")
        proj = get_project()
        src = proj.db.get_source(int(sid))
        if src is None:
            return ""
        if mode == "pdf":
            if not src.pdf_path:
                return dbc.Alert("No PDF linked for this source.", color="warning")
            return html.Iframe(src=f"/pdf/{sid}", style={"width": "100%", "height": "80vh", "border": "none"})
        md_text = None
        if src.markdown_path:
            p = Path(src.markdown_path)
            if not p.is_absolute():
                p = proj.root / p
            if p.exists():
                md_text = p.read_text(encoding="utf-8")
        if not md_text:
            return dbc.Alert("No markdown yet for this source.", color="info")
        return dcc.Markdown(md_text, style={"maxHeight": "80vh", "overflow": "auto"})

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
            import time as _t
            return dbc.Alert(st["summary"], color="success", className="py-1 mb-0"), True, {"ts": _t.time()}
        return no_update, True, no_update

    @app.callback(
        Output("extract-source-card", "children"),
        Output("extract-form-container", "children"),
        Output("extract-ai-panel", "children"),
        Output("extract-progress", "children"),
        Output("extract-store", "data"),
        Output("extract-feedback", "children"),
        Output("extract-current-sid", "data"),
        Input("extract-prev", "n_clicks"),
        Input("extract-next", "n_clicks"),
        Input("extract-submit", "n_clicks"),
        Input("extract-move-ft", "n_clicks"),
        Input("extract-move-screen", "n_clicks"),
        Input("extract-duplicate", "n_clicks"),
        Input("extract-filter", "value"),
        State("extract-refresh", "data"),
        State("shared-reviewer", "value"),
        State("extract-store", "data"),
        State({"type": "ex-value", "field": ALL}, "value"),
        State({"type": "ex-value", "field": ALL}, "id"),
        State({"type": "ex-quote", "field": ALL}, "value"),
        State({"type": "ex-quote", "field": ALL}, "id"),
        State({"type": "ex-grid", "field": ALL}, "rowData"),
        State({"type": "ex-grid", "field": ALL}, "id"),
    )
    def _update(prev, nxt, submit, move_ft, move_screen, dup, filt, _ai_refresh, reviewer, store,
                val_values, val_ids, quote_values, quote_ids, grid_rows, grid_ids):
        project = get_project()
        db = project.db
        pid = project.project_id
        rid = (reviewer or "").strip()
        trigger = ctx.triggered_id
        feedback = ""

        if not rid:
            msg = dbc.Alert("Enter your reviewer ID above to begin.", color="info")
            return msg, "", "", "", {"idx": 0}, "", None

        schema_path = project.root / project.config.extraction.schema_path
        fields = compose_schema(schema_path)

        submit_blocked = False
        if trigger == "extract-submit":
            sources = _filtered(db, pid, filt)
            idx = (store or {}).get("idx", 0)
            if 0 <= idx < len(sources):
                src = sources[idx]
                other = (
                    db.other_human_extracted(src.id, rid)
                    if project.config.extraction.workflow == "verify"
                    else None
                )
                if other:
                    submit_blocked = True
                    feedback = dbc.Alert(
                        [
                            html.Span(f"#{src.id} was already extracted by ", className="me-1"),
                            html.Strong(other),
                            html.Span(" — your changes were not saved (verify mode: one human per paper).", className="ms-1"),
                        ],
                        color="warning",
                        className="py-2 mb-0",
                    )
                else:
                    ai_rows = {r["field_name"]: r for r in db.list_extractions(src.id, extractor_type="ai")}
                    _save_extraction(
                        db, src, rid, fields,
                        val_values, val_ids, quote_values, quote_ids, grid_rows, grid_ids,
                        ai_rows=ai_rows,
                    )
                    feedback = "Extraction saved."

        if trigger == "extract-move-ft":
            sources = _filtered(db, pid, filt)
            idx = (store or {}).get("idx", 0)
            if 0 <= idx < len(sources):
                src = sources[idx]
                db.delete_stage_decisions(src.id, "full_text", reviewer_type="human")
                db.delete_reconciliations_for_source(src.id, "full_text_screening")
                db.insert_screening_action(src.id, rid, action="move_to_full_text")
                feedback = f"Moved #{src.id} back to full-text review."

        if trigger == "extract-move-screen":
            sources = _filtered(db, pid, filt)
            idx = (store or {}).get("idx", 0)
            if 0 <= idx < len(sources):
                src = sources[idx]
                db.delete_all_screening_decisions(src.id, reviewer_type="human")
                db.delete_reconciliations_for_source(src.id)
                db.insert_screening_action(src.id, rid or "?", action="move_to_screening")
                feedback = f"Moved #{src.id} back to abstract screening."

        if trigger == "extract-duplicate":
            sources = _filtered(db, pid, filt)
            idx = (store or {}).get("idx", 0)
            if 0 <= idx < len(sources):
                src = sources[idx]
                db.mark_source_duplicate(src.id, True)
                feedback = f"Marked #{src.id} as duplicate (hidden)."

        sources = _filtered(db, pid, filt)
        idx = (store or {}).get("idx", 0)

        if trigger == "extract-prev":
            idx -= 1
        elif trigger == "extract-next":
            idx += 1
        elif trigger in ("shared-reviewer", "extract-filter"):
            idx = 0
        elif trigger == "extract-submit" and not submit_blocked:
            idx += 1

        if not sources:
            done = dbc.Alert(
                "No sources qualify for extraction yet (need a full-text 'include' with markdown).", color="warning"
            )
            return done, "", "", "0 / 0", {"idx": 0}, feedback, None

        idx = max(0, min(idx, len(sources) - 1))
        src = sources[idx]
        progress = f"Source {idx + 1} / {len(sources)} — DB id {src.id}"

        workflow = project.config.extraction.workflow
        ai_data: dict[str, Any] | None = None
        if workflow == "verify":
            ai_data = {r["field_name"]: r["value"] for r in db.list_extractions(src.id, extractor_type="ai")}
        # Editable fields prefill from THIS reviewer's saved values (overriding AI); the AI
        # value stays visible as the "AI proposed" reference. Latest row wins (ORDER BY id).
        human_data = {
            r["field_name"]: r["value"]
            for r in db.list_extractions(src.id, extractor_type="human")
            if r.get("extractor_id") == rid
        }
        prefill_data = {**(ai_data or {}), **human_data}

        return (
            _source_card(project.root, src),
            _build_form([f for f in fields if f.verify], prefill_data=prefill_data, ai_data=ai_data),
            _ai_panel(db, src, workflow, rid),
            progress,
            {"idx": idx},
            feedback,
            src.id,
        )

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


def _filtered(db: Any, pid: int, filt: str) -> list[Source]:
    if filt == "all":
        return db.list_sources_with_markdown(pid)
    return db.list_full_text_final_includes_with_markdown(pid)


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
        column_defs = [{"field": s.name, "editable": True} for s in item_fields]
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
                        defaultColDef={"editable": True, "resizable": True, "sortable": True},
                        dashGridOptions={"rowSelection": "multiple", "domLayout": "autoHeight"},
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


def _leaf_widget(field: FieldSpec, dotted: str, prefill_cell: Any = None, ai_cell: Any = None) -> Any:
    label = html.Strong(field.name + (" *" if field.required else ""))
    ai_value, _ai_quote = _unwrap_cell(ai_cell)
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
        value_widget = dbc.Input(
            id={"type": "ex-value", "field": dotted},
            type="number",
            step=1 if field.type == "integer" else "any",
            **common,
        )
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

    children: list[Any] = [label, _desc(field), value_widget]
    if ai_value is not None:
        children.append(
            html.Small(
                f"AI proposed: {ai_value}",
                className="text-muted",
                style={"fontStyle": "italic"},
            )
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
    return html.Div(children, className="mb-2")


def _desc(field: FieldSpec) -> Any:
    if field.description:
        return html.P(field.description, className="text-muted small mb-1")
    return html.Span()


def _ai_panel(db: Any, src: Source, workflow: str, rid: str) -> Any:
    ai_rows = db.list_extractions(src.id, extractor_type="ai")
    flag_check = db.get_flag_check(src.id, extractor_type="ai")
    if not ai_rows and not flag_check:
        return html.P("No AI extraction yet.", className="text-muted small")

    # In `independent` mode, AI is hidden until current human commits.
    # In `verify` mode, AI is always shown (and Batch 2 will pre-fill the form from it).
    if workflow == "independent" and not db.has_extraction(src.id, "human"):
        return dbc.Alert("AI extraction hidden until you submit (workflow: independent).", color="secondary")

    items: list[Any] = [html.H6("AI extraction")]
    for row in ai_rows:
        items.append(
            html.P(
                [html.Strong(f"{row['field_name']}: "), html.Code(json.dumps(row["value"], ensure_ascii=False)[:200])],
                className="small mb-1",
            )
        )
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
) -> None:
    values: dict[str, Any] = {vid["field"]: v for vid, v in zip(val_ids, val_values)}
    quotes: dict[str, Any] = {qid["field"]: q for qid, q in zip(quote_ids, quote_values)}
    grids: dict[str, Any] = {gid["field"]: r for gid, r in zip(grid_ids, grid_rows)}
    ai_rows = ai_rows or {}

    for field in fields:
        # Fields not flagged for human verification take the AI value as-is.
        if not field.verify:
            row = ai_rows.get(field.name)
            if row is not None:
                db.insert_extraction(
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
            db.insert_extraction(
                ExtractionResult(
                    extractor_type="human", extractor_id=rid,
                    field_name=field.name, value=obj, source_id=src.id,
                    prompt_version="manual",
                )
            )
        elif field.type == "list" and field.item_type == "object":
            db.insert_extraction(
                ExtractionResult(
                    extractor_type="human", extractor_id=rid,
                    field_name=field.name, value=grids.get(field.name, []),
                    source_id=src.id, prompt_version="manual",
                )
            )
        elif field.type == "list":
            raw = values.get(field.name) or ""
            items = [line.strip() for line in str(raw).splitlines() if line.strip()]
            db.insert_extraction(
                ExtractionResult(
                    extractor_type="human", extractor_id=rid,
                    field_name=field.name, value=items, source_id=src.id,
                    prompt_version="manual",
                )
            )
        else:
            db.insert_extraction(
                ExtractionResult(
                    extractor_type="human", extractor_id=rid,
                    field_name=field.name, value=values.get(field.name),
                    source_quote=quotes.get(field.name), source_id=src.id,
                    prompt_version="manual",
                )
            )
