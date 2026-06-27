"""Full-text review: card list, stage='full_text'.

Candidates = sources marked 'include' at abstract stage AND with markdown available.
Shares workflow (assisted/independent) with abstract screening.
AI's verdict at this stage is derived from extraction.flag_check.
"""

import json
from typing import Any, Optional

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update

from ailr.core.source import Source
from ailr.reviewers import ScreeningDecision
from ailr.ui import ai_runner
from ailr.ui._common import get_project, reload_project, triggered_click_id
from ailr.ui.screen_view import (
    _SORT_OPTIONS,
    _WITHIN_OPTIONS,
    _history_block,
    _short_author_year,
)

_STATUS_FILTERS = [
    {"label": "To review", "value": "to_review"},
    {"label": "Reviewed by me", "value": "reviewed"},
    {"label": "To extract", "value": "to_extract"},
    {"label": "Extracted by me", "value": "extracted_mine"},
    {"label": "All", "value": "all"},
]


def pdf_tools_panel() -> list[Any]:
    """Full-text data-prep as clear steps: 1) link PDFs → 2) convert to markdown (or 3) import).
    Rendered on the full-text Workflow tab."""
    return [
        # ── Step 1 — PDFs (auto-linked from data/pdfs) ───────────────────────
        dbc.Label("Step 1 — PDFs", className="fw-bold"),
        html.P("Export your Zotero library (Export → Format: RIS, with 'Export Files' checked) into this project's data/pdfs folder. PDFs are linked automatically when you open the full-text pages — no path to enter, and the link travels with the shared project.", className="text-muted small mb-1"),
        dbc.Button("Re-scan data/pdfs", id="ft-linkpdf-run", color="secondary", outline=True, size="sm"),
        html.Div(id="ft-linkpdf-status", className="small mt-2"),
        # ── Step 2 — Convert PDFs to markdown ────────────────────────────────
        html.Hr(className="my-3"),
        dbc.Label("Step 2 — Convert PDFs to markdown", className="fw-bold"),
        dbc.InputGroup(
            [
                dbc.InputGroupText("Low-text warning threshold (chars)"),
                dbc.Input(id="ft-low-text-threshold", type="number", min=0, step=100, value=get_project().config.preprocess.low_text_threshold, size="sm", style={"maxWidth": "120px"}),
            ],
            size="sm",
            className="mb-2",
        ),
        html.P("Converted markdown shorter than this is flagged as likely scanned/failed (saved when you convert).", className="text-muted small mb-1"),
        dbc.Button("Convert PDFs to markdown", id="ft-preprocess-run", color="secondary", outline=True, size="sm"),
        html.Div(id="ft-preprocess-status", className="small mt-2"),
        dcc.Interval(id="ft-preprocess-poll", interval=1500, disabled=True),
        # ── Step 3 (optional) — import converted .md instead ─────────────────
        html.Hr(className="my-3"),
        dbc.Label("Step 3 (optional) — …or import converted .md", className="fw-bold"),
        html.P("Use this instead of Step 2 if you converted the PDFs to markdown elsewhere.", className="text-muted small mb-1"),
        dbc.Input(id="ft-md-folder", placeholder="Paste a folder path of .md files", size="sm", className="mb-1"),
        dbc.Button("Import markdown from folder", id="ft-md-import", color="secondary", outline=True, size="sm"),
        html.Div(id="ft-md-import-status", className="small mt-2"),
    ]


def layout() -> Any:
    project = get_project()
    return dbc.Row(
        [
            dbc.Col(
                [
                    dbc.Label("Status", className="fw-bold"),
                    dbc.RadioItems(
                        id="ft-filter-status",
                        options=_STATUS_FILTERS,
                        value="to_review",
                        persistence=True,
                        persistence_type="session",
                    ),

                    dbc.Label("Sort", className="fw-bold mt-3"),
                    dbc.Select(id="ft-sort", options=_SORT_OPTIONS, value="id", persistence=True, persistence_type="session"),

                    dbc.Label("Tags", className="fw-bold mt-2"),
                    dbc.Select(
                        id="ft-tags-filter",
                        options=[{"label": "(any)", "value": ""}],
                        value="",
                        persistence=True,
                        persistence_type="session",
                    ),

                    dbc.Label("Keyword search", className="fw-bold mt-2"),
                    dbc.Input(
                        id="ft-search",
                        placeholder="Type and press Enter",
                        debounce=True,
                        persistence=True,
                        persistence_type="session",
                    ),

                    dbc.Label("Within", className="small mt-1"),
                    dbc.RadioItems(
                        id="ft-within",
                        options=_WITHIN_OPTIONS,
                        value="title_and_abstract",
                        className="mb-2",
                        persistence=True,
                        persistence_type="session",
                    ),

                    dbc.Label("Full-text", className="small"),
                    dbc.Checklist(
                        id="ft-ftavail-filter",
                        options=[
                            {"label": "Has full-text", "value": "has"},
                            {"label": "Needs full-text", "value": "needs"},
                        ],
                        value=["has"],
                        className="mb-2",
                        persistence=True,
                        persistence_type="session",
                    ),

                    dbc.Label("Display", className="fw-bold mt-2"),
                    dbc.Select(
                        id="ft-pagesize",
                        options=[
                            {"label": "25 per page", "value": "25"},
                            {"label": "50 per page", "value": "50"},
                            {"label": "100 per page", "value": "100"},
                        ],
                        value="25",
                        persistence=True,
                        persistence_type="session",
                    ),

                    dbc.Switch(
                        id="ft-expand-all",
                        label="Expand all abstracts",
                        value=True,
                        className="mt-2",
                        label_class_name="fw-bold",
                        persistence=True,
                        persistence_type="session",
                    ),

                    dbc.Button(
                        "↻ Reset filters",
                        id="ft-reset-filters",
                        color="secondary",
                        outline=True,
                        size="sm",
                        className="w-100 mt-2",
                    ),

                    html.Hr(),
                    html.Div(id="ft-counts", className="small text-muted"),
                ],
                width=3,
            ),
            dbc.Col(
                [
                    html.Div(id="ft-action-banner"),
                    html.Div(id="ft-cards"),
                    html.Div(
                        [
                            dbc.Button("← Prev", id="ft-page-prev", disabled=True, color="secondary", outline=True, size="sm", className="me-2"),
                            html.Span(id="ft-page-info", className="text-muted small"),
                            dbc.Button("Next →", id="ft-page-next", disabled=True, color="secondary", outline=True, size="sm", className="ms-2"),
                        ],
                        className="d-flex justify-content-center align-items-center mt-3",
                    ),
                ],
                width=9,
            ),
        ]
    )


def register_callbacks(app: Any) -> None:
    @app.callback(
        Output("tabs", "data", allow_duplicate=True),
        Output("extract-store", "data", allow_duplicate=True),
        Input({"type": "ft-open-extract", "source": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def _open_extraction(_clicks):
        trig = ctx.triggered_id
        if not isinstance(trig, dict) or not any(c.get("value") for c in (ctx.triggered or [])):
            return no_update, no_update
        # The extraction view is driven purely by this source id (no positional index), so opening a
        # card always lands on exactly that paper regardless of list order or page re-mounts.
        return "extract", {"sid": trig.get("source")}

    @app.callback(
        Output("ft-preprocess-poll", "disabled"),
        Output("ft-preprocess-status", "children"),
        Input("ft-preprocess-run", "n_clicks"),
        State("ft-low-text-threshold", "value"),
        prevent_initial_call=True,
    )
    def _preprocess_run(n, threshold):
        if not n:
            return no_update, no_update
        project = get_project()
        if threshold is not None and int(threshold) != project.config.preprocess.low_text_threshold:
            from ailr.core.config import save_preprocess_threshold
            save_preprocess_threshold(project.root, int(threshold))
            project = reload_project()
        started = ai_runner.start_preprocess(project)
        msg = "Converting…" if started else "Already running…"
        return False, dbc.Alert(msg, color="info", className="py-1 mb-0")

    @app.callback(
        Output("ft-linkpdf-status", "children"),
        Output("ft-refresh", "data", allow_duplicate=True),
        Input("ft-linkpdf-run", "n_clicks"),
        prevent_initial_call=True,
    )
    def _link_pdfs(n):
        if not n:
            return no_update, no_update
        from ailr.ingest.pdf_link import auto_link_pdfs

        try:
            s = auto_link_pdfs(get_project(), force=True)
        except Exception as e:
            return dbc.Alert(f"Failed: {e}", color="danger", className="py-1 mb-0"), no_update
        if s.total_records == 0:
            return dbc.Alert("No Zotero .ris found in data/pdfs. Export your library there (with 'Export Files').", color="warning", className="py-1 mb-0"), no_update
        import time as _t
        msg = f"Newly linked {s.linked}, already linked {s.already_linked}, unmatched {len(s.unmatched)}, missing files {len(s.missing_files)}."
        return dbc.Alert(msg, color="success", className="py-1 mb-0"), {"ts": _t.time()}

    @app.callback(
        Output("ft-md-import-status", "children"),
        Output("ft-refresh", "data", allow_duplicate=True),
        Input("ft-md-import", "n_clicks"),
        State("ft-md-folder", "value"),
        prevent_initial_call=True,
    )
    def _import_md(n, folder):
        if not n:
            return no_update, no_update
        if not folder or not folder.strip():
            return dbc.Alert("Paste a folder path first.", color="warning", className="py-1 mb-0"), no_update
        from ailr.tasks.preprocess import import_markdown_from_folder

        try:
            r = import_markdown_from_folder(get_project(), folder.strip())
        except Exception as e:
            return dbc.Alert(f"Import failed: {e}", color="danger", className="py-1 mb-0"), no_update
        import time as _t
        msg = f"Imported {r['matched']} markdown file(s). Found {r['md_files_found']} .md, {len(r['unmatched'])} unmatched, {r['no_pdf_path']} source(s) without a linked PDF."
        return dbc.Alert(msg, color="success", className="py-1 mb-0"), {"ts": _t.time()}

    @app.callback(
        Output("ft-preprocess-status", "children", allow_duplicate=True),
        Output("ft-preprocess-poll", "disabled", allow_duplicate=True),
        Output("ft-refresh", "data", allow_duplicate=True),
        Input("ft-preprocess-poll", "n_intervals"),
        prevent_initial_call=True,
    )
    def _preprocess_poll(_n):
        st = ai_runner.get_status("preprocess")
        if st.get("running"):
            done, total = st.get("done", 0), st.get("total", 0)
            pct = int(done / total * 100) if total else 0
            bar = dbc.Progress(value=pct, label=f"{done}/{total}", striped=True, animated=True, className="mt-1")
            return html.Div(["Converting PDFs…", bar]), False, no_update
        if st.get("error"):
            return dbc.Alert(f"Convert failed: {st['error']}", color="danger", className="py-1 mb-0"), True, no_update
        if st.get("started") and st.get("summary"):
            import time as _t
            return dbc.Alert(st["summary"], color="success", className="py-1 mb-0"), True, {"ts": _t.time()}
        return no_update, True, no_update

    @app.callback(
        Output("ft-refresh", "data"),
        Output("ft-last-action", "data"),
        Input({"type": "ft-decide", "source": ALL, "decision": ALL}, "n_clicks"),
        Input({"type": "ft-reset", "source": ALL}, "n_clicks"),
        State("shared-reviewer", "value"),
        prevent_initial_call=True,
    )
    def _on_action(_d, _r, reviewer):
        rid = (reviewer or "").strip()
        # Use the button that actually carries the click value (not ctx.triggered_id, which can point
        # at a value-less freshly-rendered button) so the decision always lands on the clicked paper.
        clicked = next((c for c in (ctx.triggered or []) if c.get("value")), None)
        if clicked is None or not rid:
            return no_update, no_update
        triggered = json.loads(clicked["prop_id"].rsplit(".", 1)[0])

        import time as _t
        db = get_project().db

        if isinstance(triggered, dict) and triggered.get("type") == "ft-decide":
            source_id = int(triggered["source"])
            decision = triggered["decision"]
            # Vote lock in one query: skip my double-click; cap team size (assisted 1 + AI, independent 2).
            i_voted, others = db.screening_lock_check(source_id, rid, "full_text")
            if i_voted:
                return {"ts": _t.time()}, no_update
            team_humans = 1 if get_project().config.screening.workflow == "assisted" else 2
            if others >= team_humans:
                other = db.other_human_decided(source_id, "full_text", rid) or "another reviewer"
                return {"ts": _t.time()}, {"blocked": True, "by": other, "sid": source_id, "ts": _t.time()}
            with db._conn.transaction():  # decision + action in one commit
                db.insert_screening_decision(
                    ScreeningDecision(
                        decision=decision,
                        reasoning="(full-text review)",
                        reviewer_type="human",
                        reviewer_id=rid,
                        source_id=source_id,
                        stage="full_text",
                    )
                )
                db.insert_screening_action(source_id, rid, action="vote", decision=decision)
            src = db.get_source(source_id)
            return {"ts": _t.time()}, {
                "sid": source_id,
                "decision": decision,
                "author_year": _short_author_year(src) if src else "",
                "title": src.title if src else "",
                "ts": _t.time(),
            }

        if isinstance(triggered, dict) and triggered.get("type") == "ft-reset":
            source_id = int(triggered["source"])
            db.delete_screening_decision(source_id, rid, stage="full_text", reviewer_type="human")
            db.delete_reconciliations_for_source(source_id, "full_text_screening")
            db.insert_screening_action(source_id, rid, action="reset")
            return {"ts": _t.time()}, None

        return {"ts": _t.time()}, no_update

    @app.callback(
        Output("ft-action-banner", "children"),
        Input("ft-last-action", "data"),
    )
    def _render_banner(last):
        if not last or not isinstance(last, dict):
            return ""
        if last.get("blocked"):
            return dbc.Alert(
                [
                    html.Span(f"#{last.get('sid')} was already reviewed by ", className="me-1"),
                    html.Strong(str(last.get("by", "another reviewer"))),
                    html.Span(" — your vote was skipped (assisted mode: one human per paper).", className="ms-1"),
                ],
                color="warning",
                className="py-2 mb-2",
            )
        decision = last.get("decision", "")
        sid = last.get("sid")
        author_year = last.get("author_year", "")
        title = last.get("title", "")
        title_short = (title[:80] + "…") if len(title) > 80 else title
        color_map = {"include": "success", "exclude": "danger", "uncertain": "warning"}
        return dbc.Alert(
            [
                html.Span("Saved ", className="me-1"),
                dbc.Badge(decision.upper(), color=color_map.get(decision, "secondary"), className="me-2"),
                html.Strong(f"#{sid} ", className="me-1"),
                html.Span(f"{author_year} ", className="me-2") if author_year else None,
                html.Em(f"“{title_short}”", className="me-3 text-muted small") if title_short else None,
                dbc.Button("Undo", id="ft-banner-undo", color="link", size="sm", className="p-0"),
            ],
            color="light",
            className="py-2 mb-2 d-flex align-items-center flex-wrap",
        )

    @app.callback(
        Output("ft-refresh", "data", allow_duplicate=True),
        Output("ft-last-action", "data", allow_duplicate=True),
        Input("ft-banner-undo", "n_clicks"),
        State("ft-last-action", "data"),
        State("shared-reviewer", "value"),
        prevent_initial_call=True,
    )
    def _undo(_c, last, reviewer):
        if not _c:  # ignore the auto-fire when the banner (and its Undo button) is re-created
            return no_update, no_update
        if not last or not isinstance(last, dict):
            return no_update, no_update
        rid = (reviewer or "").strip()
        if not rid:
            return no_update, no_update
        sid = last.get("sid")
        if sid is None:
            return no_update, no_update
        import time as _t
        db = get_project().db
        db.delete_screening_decision(int(sid), rid, stage="full_text", reviewer_type="human")
        db.delete_reconciliations_for_source(int(sid), "full_text_screening")
        db.insert_screening_action(int(sid), rid, action="reset")
        return {"ts": _t.time()}, None

    @app.callback(
        Output("ft-refresh", "data", allow_duplicate=True),
        Input({"type": "ft-duplicate", "source": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def _on_ft_mark_duplicate(_clicks):
        triggered = triggered_click_id()
        if triggered is None:
            return no_update
        import time as _t
        get_project().db.mark_source_duplicate(int(triggered["source"]), True)
        return {"ts": _t.time()}

    @app.callback(
        Output("ft-refresh", "data", allow_duplicate=True),
        Input({"type": "ft-move-screen", "source": ALL}, "n_clicks"),
        State("shared-reviewer", "value"),
        prevent_initial_call=True,
    )
    def _on_move_to_screening(_clicks, reviewer):
        triggered = triggered_click_id()
        if triggered is None:
            return no_update
        import time as _t
        sid = int(triggered["source"])
        db = get_project().db
        db.delete_all_screening_decisions(sid, reviewer_type="human")
        db.delete_reconciliations_for_source(sid)
        db.insert_screening_action(sid, (reviewer or "").strip() or "?", action="move_to_screening")
        return {"ts": _t.time()}

    @app.callback(
        Output("ft-exclude-modal", "is_open"),
        Output("ft-exclude-title", "children"),
        Output("ft-exclude-choices", "options"),
        Output("ft-exclude-choices", "value"),
        Output("ft-exclude-source", "data"),
        Output("ft-exclude-feedback", "children"),
        Input({"type": "ft-exclude-open", "source": ALL}, "n_clicks"),
        Input("ft-exclude-cancel", "n_clicks"),
        prevent_initial_call=True,
    )
    def _open_exclude(_open, _cancel):
        if ctx.triggered_id == "ft-exclude-cancel":
            return False, no_update, no_update, no_update, no_update, no_update
        triggered = triggered_click_id()
        if triggered is None:
            return (no_update,) * 6
        sid = int(triggered["source"])
        proj = get_project()
        opts = [{"label": r["name"], "value": r["name"]} for r in proj.db.list_exclusion_reasons(proj.project_id)]
        return True, f"Exclude #{sid}", opts, [], {"sid": sid}, ""

    @app.callback(
        Output("ft-exclude-choices", "options", allow_duplicate=True),
        Output("ft-exclude-choices", "value", allow_duplicate=True),
        Output("ft-exclude-new", "value"),
        Input("ft-exclude-add", "n_clicks"),
        State("ft-exclude-new", "value"),
        State("ft-exclude-choices", "value"),
        prevent_initial_call=True,
    )
    def _add_reason(_n, name, current):
        if not _n or not name or not name.strip():
            return no_update, no_update, no_update
        proj = get_project()
        clean = name.strip()
        proj.db.create_exclusion_reason(proj.project_id, clean)
        opts = [{"label": r["name"], "value": r["name"]} for r in proj.db.list_exclusion_reasons(proj.project_id)]
        selected = list(current or [])
        if clean not in selected:
            selected.append(clean)
        return opts, selected, ""

    @app.callback(
        Output("ft-refresh", "data", allow_duplicate=True),
        Output("ft-last-action", "data", allow_duplicate=True),
        Output("ft-exclude-modal", "is_open", allow_duplicate=True),
        Output("ft-exclude-feedback", "children", allow_duplicate=True),
        Input("ft-exclude-confirm", "n_clicks"),
        State("ft-exclude-choices", "value"),
        State("ft-exclude-source", "data"),
        State("shared-reviewer", "value"),
        prevent_initial_call=True,
    )
    def _confirm_exclude(_n, reasons, data, reviewer):
        if not _n or not data:
            return no_update, no_update, no_update, no_update
        rid = (reviewer or "").strip()
        if not rid:
            return no_update, no_update, no_update, dbc.Alert("Enter your reviewer ID first.", color="warning", className="mb-0 py-1")
        if not reasons:
            return no_update, no_update, no_update, dbc.Alert("Pick or add at least one reason.", color="warning", className="mb-0 py-1")
        reason = "; ".join(reasons) if isinstance(reasons, list) else str(reasons)
        import time as _t
        sid = int(data["sid"])
        db = get_project().db
        if get_project().config.screening.workflow == "assisted":
            other = db.other_human_decided(sid, "full_text", rid)
            if other:
                return {"ts": _t.time()}, {"blocked": True, "by": other, "sid": sid, "ts": _t.time()}, False, ""
        db.insert_screening_decision(
            ScreeningDecision(
                decision="exclude",
                reasoning=reason,
                reviewer_type="human",
                reviewer_id=rid,
                source_id=sid,
                stage="full_text",
            )
        )
        db.insert_screening_action(sid, rid, action="vote", decision="exclude")
        src = db.get_source(sid)
        last = {
            "sid": sid,
            "decision": "exclude",
            "author_year": _short_author_year(src) if src else "",
            "title": src.title if src else "",
            "ts": _t.time(),
        }
        return {"ts": _t.time()}, last, False, ""

    @app.callback(
        Output("ft-tags-filter", "options"),
        Input("tabs", "data"),
        Input("tags-refresh", "data"),
    )
    def _populate_ft_tag_options(tab, _refresh):
        if tab != "full_text":
            return no_update
        project = get_project()
        opts = [{"label": "(any)", "value": ""}]
        opts.extend({"label": t["name"], "value": str(t["id"])} for t in project.db.list_tags(project.project_id))
        return opts

    @app.callback(
        Output("ft-page", "data"),
        Input("ft-page-prev", "n_clicks"),
        Input("ft-page-next", "n_clicks"),
        Input("ft-filter-status", "value"),
        Input("ft-search", "value"),
        Input("ft-tags-filter", "value"),
        Input("ft-sort", "value"),
        Input("ft-pagesize", "value"),
        Input("shared-reviewer", "value"),
        State("ft-page", "data"),
        prevent_initial_call=True,
    )
    def _page_nav(_prev, _next, _s, _kw, _tg, _sort, _ps, _rev, current):
        trigger = ctx.triggered_id
        page = (current or {}).get("page", 0)
        if trigger == "ft-page-prev":
            return {"page": max(0, page - 1)}
        if trigger == "ft-page-next":
            return {"page": page + 1}
        return {"page": 0}

    @app.callback(
        Output("ft-search", "value"),
        Output("ft-within", "value"),
        Output("ft-ftavail-filter", "value"),
        Output("ft-filter-status", "value"),
        Input("ft-reset-filters", "n_clicks"),
        prevent_initial_call=True,
    )
    def _ft_reset_filters(_clicks):
        return "", "title_and_abstract", ["has"], "to_review"

    @app.callback(
        Output("ft-cards", "children"),
        Output("ft-counts", "children"),
        Output("ft-page-prev", "disabled"),
        Output("ft-page-next", "disabled"),
        Output("ft-page-info", "children"),
        Input("ft-filter-status", "value"),
        Input("ft-refresh", "data"),
        Input("shared-reviewer", "value"),
        Input("tags-refresh", "data"),
        Input("notes-refresh", "data"),
        Input("ft-search", "value"),
        Input("ft-within", "value"),
        Input("ft-ftavail-filter", "value"),
        Input("ft-tags-filter", "value"),
        Input("ft-sort", "value"),
        Input("ft-pagesize", "value"),
        Input("ft-page", "data"),
        Input("ft-expand-all", "value"),
    )
    def _render(status, _refresh, reviewer, _tags, _notes, search, within, ftavail, tag_filter, sort_by, pagesize, page_state, expand_all):
        project = get_project()
        db = project.db
        pid = project.project_id
        rid = (reviewer or "").strip()
        workflow = project.config.screening.workflow

        if not rid:
            return [dbc.Alert("Enter your reviewer ID above to begin.", color="info")], "", True, True, ""

        team_size = 2 if workflow == "independent" else 1
        try:
            psize = int(pagesize)
        except (TypeError, ValueError):
            psize = 25
        try:
            tag_id = int(tag_filter) if tag_filter else None
        except (TypeError, ValueError):
            tag_id = None
        ft_set = set(ftavail or [])
        ft_avail = "has" if ("has" in ft_set and "needs" not in ft_set) else ("needs" if ("needs" in ft_set and "has" not in ft_set) else None)
        req_page = (page_state or {}).get("page", 0)

        total_candidates = db.count_full_text_candidates(pid)
        if total_candidates == 0:
            return (
                [
                    dbc.Alert(
                        "No sources qualify for full-text review yet. "
                        "Need at least one source marked 'include' at the abstract stage. "
                        "Use the ‘Needs full-text’ filter to see included papers still awaiting their PDF/markdown.",
                        color="info",
                    )
                ],
                "0 candidates",
                True,
                True,
                "",
            )

        # Filter + sort + paginate in SQL: only this page's rows come back, not all candidates.
        page_sources, total, page = db.list_full_text_page(
            pid, rid, status=status, keyword=search or "", within=within or "title_and_abstract",
            tag_id=tag_id, ft_avail=ft_avail, team_size=team_size, sort_by=sort_by or "id",
            page=req_page, page_size=psize,
        )

        page_ids = [s.id for s in page_sources if s.id is not None]
        my_decisions = db.get_decisions_by_reviewer(page_ids, rid, stage="full_text")
        peer_counts = db.count_peer_reviewers(page_ids, rid, stage="full_text") if workflow == "independent" else {}
        extract_ids = db.final_include_md_ids(page_ids)            # which of this page are extraction-eligible
        extracted_by = db.human_extractors_for_sources(page_ids)  # {sid: extractor_id who submitted}
        tags_by_source = db.get_tags_for_sources(page_ids)
        ai_by_source = db.get_latest_ai_decisions(page_ids, stage="full_text")
        note_counts = db.count_notes(page_ids)

        cards = [
            _ft_card(
                s, my_decisions.get(s.id), workflow, peer_counts.get(s.id, 0), rid,
                can_extract=s.id in extract_ids, expand_abstract=bool(expand_all),
                extracted_by=extracted_by.get(s.id),
                extract_verify=project.config.extraction.workflow == "verify",
                low_text=_low_text_md(project.root, s.id, project.config.preprocess.low_text_threshold),
                tags=tags_by_source.get(s.id, []),
                ai_decision=ai_by_source.get(s.id),
                note_count=note_counts.get(s.id, 0),
            )
            for s in page_sources
        ]
        if not cards:
            cards = [dbc.Alert("No sources match the current filter.", color="success")]

        total_pages = max(1, (total + psize - 1) // psize)
        prev_disabled = page <= 0
        next_disabled = page >= total_pages - 1
        page_info = f"Page {page + 1} of {total_pages}  ({total} total)" if total else ""
        n_reviewed = db.count_reviewer_decisions(pid, rid, stage="full_text")
        counts = f"{n_reviewed} / {total_candidates} reviewed by you • {total} match current filter"
        return cards, counts, prev_disabled, next_disabled, page_info


def _low_text_md(root: Any, sid: Any, threshold: int) -> bool:
    p = root / "data" / "markdown" / f"{sid}.md"
    try:
        return p.is_file() and p.stat().st_size < threshold
    except OSError:
        return False


def _ft_card(
    src: Source,
    my_decision: Optional[str],
    workflow: str,
    peer_count: int,
    reviewer_id: str,
    can_extract: bool = False,
    expand_abstract: bool = False,
    extracted_by: Optional[str] = None,
    extract_verify: bool = False,
    low_text: bool = False,
    tags: Optional[list[dict]] = None,
    ai_decision: Optional[str] = None,
    note_count: int = 0,
) -> Any:
    sid = src.id
    decision_color = {"include": "success", "exclude": "danger", "uncertain": "warning"}

    if my_decision:
        right = [
            dbc.Badge(
                my_decision.upper(),
                color=decision_color.get(my_decision, "secondary"),
                className="me-2 p-2",
                style={"fontSize": "0.9rem"},
            ),
            dbc.Button(
                "Reset",
                id={"type": "ft-reset", "source": sid},
                size="sm",
                color="link",
                className="p-0 text-decoration-none",
            ),
        ]
    else:
        right = [
            dbc.ButtonGroup(
                [
                    dbc.Button("Include", id={"type": "ft-decide", "source": sid, "decision": "include"}, color="success", size="sm"),
                    dbc.Button("Exclude", id={"type": "ft-exclude-open", "source": sid}, color="danger", size="sm"),
                    dbc.Button("Uncertain", id={"type": "ft-decide", "source": sid, "decision": "uncertain"}, color="warning", size="sm"),
                ]
            )
        ]

    peer_indicator: Any = None
    if workflow == "independent" and peer_count > 0:
        peer_indicator = html.Small(
            f"{peer_count} other reviewer(s) voted",
            className="text-muted d-block mt-1",
        )

    # AI verdict (blinding-aware): hidden until this reviewer has submitted, when blinding is on.
    ai_panel: Any = None
    if ai_decision is not None:
        if workflow != "off" and not my_decision:
            ai_panel = dbc.Alert(
                "AI already assessed — its verdict appears after you submit your decision.",
                color="secondary",
                className="py-1 mt-2 small",
            )
        else:
            ai_panel = dbc.Card(
                dbc.CardBody(
                    [
                        html.Small("AI flag_check verdict", className="fw-bold"),
                        html.Div(
                            [
                                html.Span("Decision: ", className="text-muted"),
                                dbc.Badge(
                                    ai_decision.upper(),
                                    color=decision_color.get(ai_decision, "secondary"),
                                    className="me-2",
                                ),
                            ]
                        ),
                    ],
                    className="py-2 px-2",
                ),
                color="light",
                className="mt-2",
            )

    read_btn = html.Div(
        dbc.Button(
            "Read full text",
            id={"type": "ft-read-btn", "source": sid},
            size="sm",
            color="link",
            className="p-0",
        ),
        className="mt-1",
    )

    doi_el: Any = None
    if src.doi:
        doi_el = html.Div(
            html.A(f"DOI: {src.doi}", href=f"https://doi.org/{src.doi}", target="_blank", className="small")
        )

    # Tags (display only; per-card add via the shared tag modal isn't wired here yet)
    tag_chips_el: Any = html.Span()
    if tags:
        tag_chips_el = html.Div(
            [
                dbc.Badge(t["name"], color=t.get("color") or "secondary", pill=True, className="me-1")
                for t in tags
            ],
            className="mt-1 mb-1",
        )

    actions_row = html.Div(
        [
            dbc.Button("History", id={"type": "ft-history-btn", "source": sid}, size="sm", color="link", className="p-0 me-3"),
            dbc.Button("Tags", id={"type": "ft-tag-btn", "source": sid}, size="sm", color="link", className="p-0 me-3"),
            dbc.Button(f"Note ({note_count})" if note_count else "Note", id={"type": "ft-note-btn", "source": sid}, size="sm", color="link", className="p-0 me-3"),
            dbc.Button("Duplicate", id={"type": "ft-duplicate", "source": sid}, size="sm", color="link", className="p-0 me-3 text-danger"),
            dbc.Button("↺ Move to screening", id={"type": "ft-move-screen", "source": sid}, size="sm", color="link", className="p-0 text-secondary"),
        ],
        className="mt-1",
    )

    extract_row: Any = None
    if can_extract:
        locked = extracted_by is not None and extracted_by != reviewer_id and extract_verify
        if extracted_by is None:
            status_badge = dbc.Badge("To extract", color="secondary", className="align-middle")
        elif extracted_by == reviewer_id:
            status_badge = dbc.Badge("Extracted by you", color="success", className="align-middle")
        else:
            status_badge = dbc.Badge(f"Extracted by {extracted_by}", color="success", className="align-middle")
        children: list[Any] = [
            dbc.Button(
                "View extraction →" if locked else "Open extraction →",
                id={"type": "ft-open-extract", "source": sid},
                size="sm", color="primary", outline=True, className="me-2",
            ),
            status_badge,
        ]
        extract_row = html.Div(children, className="mt-2")

    abstract_block: Any = None
    if expand_abstract and src.abstract:
        abstract_block = html.P(src.abstract, className="text-muted small mt-1 mb-1")

    low_text_badge: Any = None
    if low_text:
        low_text_badge = html.Div(
            dbc.Badge("low-text PDF — may be scanned; check or re-convert (e.g. marker)", color="warning"),
            className="mt-1",
        )

    left = html.Div(
        [
            html.Strong(f"#{sid}  ", className="text-muted"),
            html.Span(_short_author_year(src), className="text-muted me-2"),
        ]
    )
    title_el = html.H6(src.title, className="mb-1")
    meta_parts = []
    if src.journal:
        meta_parts.append(src.journal)
    if src.year:
        meta_parts.append(str(src.year))
    meta_el = html.P(" • ".join(meta_parts), className="text-muted small mb-1")

    return dbc.Card(
        dbc.CardBody(
            [
                dbc.Row(
                    [
                        dbc.Col(
                            [left, title_el, meta_el, low_text_badge, abstract_block, tag_chips_el, doi_el, read_btn, ai_panel, actions_row, extract_row],
                            width=9,
                        ),
                        dbc.Col(
                            html.Div(right + ([peer_indicator] if peer_indicator else []), className="text-end"),
                            width=3,
                        ),
                    ]
                ),
            ]
        ),
        className="mb-3",
    )
