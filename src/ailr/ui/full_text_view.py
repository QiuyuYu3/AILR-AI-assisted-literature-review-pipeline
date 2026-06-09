"""Full-text review: card list, stage='full_text'.

Candidates = sources marked 'include' at abstract stage AND with markdown available.
Shares workflow (assisted/independent) with abstract screening.
AI's verdict at this stage is derived from extraction.flag_check.
"""

from typing import Any, Optional

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update

from ailr.core.source import Source
from ailr.reviewers import ScreeningDecision
from ailr.ui import ai_runner
from ailr.ui._common import get_project
from ailr.ui.screen_view import (
    _SORT_OPTIONS,
    _WITHIN_OPTIONS,
    _apply_sort,
    _history_block,
    _kw_match,
    _note_label,
    _short_author_year,
)

_STATUS_FILTERS = [
    {"label": "To review", "value": "to_review"},
    {"label": "Reviewed by me", "value": "reviewed"},
    {"label": "All", "value": "all"},
]


def pdf_tools_panel() -> list[Any]:
    """Full-text data-prep as clear steps: 1) link PDFs → 2) convert to markdown (or 3) import).
    Rendered on the full-text Workflow tab."""
    return [
        # ── Step 1 — Link PDFs ───────────────────────────────────────────────
        dbc.Label("Step 1 — Link PDFs (Zotero RIS)", className="fw-bold"),
        html.P("Point to the Zotero .ris file (Zotero → Export → Format: RIS, with 'Export Files' checked) — not a folder of PDFs.", className="text-muted small mb-1"),
        dbc.Input(id="ft-linkpdf-path", placeholder="e.g. C:/.../test_includes/test_includes.ris", size="sm", className="mb-1"),
        dbc.Button("Link PDFs", id="ft-linkpdf-run", color="secondary", outline=True, size="sm"),
        html.Div(id="ft-linkpdf-status", className="small mt-2"),
        # ── Step 2 — Convert PDFs to markdown ────────────────────────────────
        html.Hr(className="my-3"),
        dbc.Label("Step 2 — Convert PDFs to markdown", className="fw-bold"),
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
                    ),

                    dbc.Label("Sort", className="fw-bold mt-3"),
                    dbc.Select(id="ft-sort", options=_SORT_OPTIONS, value="id"),

                    dbc.Label("Tags", className="fw-bold mt-2"),
                    dbc.Select(
                        id="ft-tags-filter",
                        options=[{"label": "(any)", "value": ""}],
                        value="",
                    ),

                    dbc.Label("Keyword search", className="fw-bold mt-2"),
                    dbc.Input(
                        id="ft-search",
                        placeholder="Type and press Enter",
                        debounce=True,
                    ),

                    dbc.Label("Within", className="small mt-1"),
                    dbc.RadioItems(
                        id="ft-within",
                        options=_WITHIN_OPTIONS,
                        value="title_and_abstract",
                        className="mb-2",
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
        Output("ft-preprocess-poll", "disabled"),
        Output("ft-preprocess-status", "children"),
        Input("ft-preprocess-run", "n_clicks"),
        prevent_initial_call=True,
    )
    def _preprocess_run(n):
        if not n:
            return no_update, no_update
        started = ai_runner.start_preprocess(get_project())
        msg = "Converting…" if started else "Already running…"
        return False, dbc.Alert(msg, color="info", className="py-1 mb-0")

    @app.callback(
        Output("ft-linkpdf-status", "children"),
        Output("ft-refresh", "data", allow_duplicate=True),
        Input("ft-linkpdf-run", "n_clicks"),
        State("ft-linkpdf-path", "value"),
        prevent_initial_call=True,
    )
    def _link_pdfs(n, path):
        if not n:
            return no_update, no_update
        from pathlib import Path as _P
        p = _P((path or "").strip())
        if not path or not p.is_file():
            if path and p.is_dir():
                msg = "That's a folder — point to the Zotero .ris file inside it, not a folder of PDFs."
            else:
                msg = "Enter the path to your Zotero .ris file (Zotero → Export → Format RIS, with 'Export Files' checked)."
            return dbc.Alert(msg, color="warning", className="py-1 mb-0"), no_update
        from ailr.ingest.pdf_link import link_pdfs_from_ris

        try:
            s = link_pdfs_from_ris(get_project(), p)
        except Exception as e:
            return dbc.Alert(f"Failed: {e}", color="danger", className="py-1 mb-0"), no_update
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
        triggered = ctx.triggered_id
        rid = (reviewer or "").strip()
        if triggered is None or not rid:
            return no_update, no_update
        any_click = any(c for c in (ctx.triggered or []) if c.get("value"))
        if not any_click:
            return no_update, no_update

        import time as _t
        db = get_project().db

        if isinstance(triggered, dict) and triggered.get("type") == "ft-decide":
            source_id = int(triggered["source"])
            decision = triggered["decision"]
            if get_project().config.screening.workflow == "assisted":
                other = db.other_human_decided(source_id, "full_text", rid)
                if other:
                    return {"ts": _t.time()}, {"blocked": True, "by": other, "sid": source_id, "ts": _t.time()}
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
            db.delete_screening_decision(source_id, rid, stage="full_text")
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
        db.delete_screening_decision(int(sid), rid, stage="full_text")
        db.insert_screening_action(int(sid), rid, action="reset")
        return {"ts": _t.time()}, None

    @app.callback(
        Output("ft-refresh", "data", allow_duplicate=True),
        Input({"type": "ft-duplicate", "source": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def _on_ft_mark_duplicate(_clicks):
        triggered = ctx.triggered_id
        if not isinstance(triggered, dict):
            return no_update
        if not any(c.get("value") for c in (ctx.triggered or [])):
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
        triggered = ctx.triggered_id
        if not isinstance(triggered, dict):
            return no_update
        if not any(c.get("value") for c in (ctx.triggered or [])):
            return no_update  # ignore auto-fire on (re-)creation
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
        triggered = ctx.triggered_id
        if triggered == "ft-exclude-cancel":
            return False, no_update, no_update, no_update, no_update, no_update
        if not isinstance(triggered, dict):
            return (no_update,) * 6
        if not any(t.get("value") for t in (ctx.triggered or [])):
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
    )
    def _render(status, _refresh, reviewer, _tags, _notes, search, within, ftavail, tag_filter, sort_by, pagesize, page_state):
        project = get_project()
        db = project.db
        pid = project.project_id
        rid = (reviewer or "").strip()
        workflow = project.config.screening.workflow

        if not rid:
            return [dbc.Alert("Enter your reviewer ID above to begin.", color="info")], "", True, True, ""

        all_candidates = db.list_sources_for_full_text(pid, require_markdown=False)
        if not all_candidates:
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

        source_ids = [s.id for s in all_candidates if s.id is not None]
        my_decisions = db.get_decisions_by_reviewer(source_ids, rid, stage="full_text")
        team_size = 2 if workflow == "independent" else 1
        human_counts = db.count_human_decisions_per_source(pid, stage="full_text")

        if status == "to_review":
            sources = [
                s for s in all_candidates
                if s.id not in my_decisions and human_counts.get(s.id, 0) < team_size
            ]
        elif status == "reviewed":
            sources = [s for s in all_candidates if s.id in my_decisions]
        else:
            sources = list(all_candidates)

        kw = (search or "").strip().lower()
        if kw:
            sources = [s for s in sources if _kw_match(s, kw, within or "title_and_abstract")]

        ft_set = set(ftavail or [])
        if "has" in ft_set and "needs" not in ft_set:
            sources = [s for s in sources if s.markdown_path]
        elif "needs" in ft_set and "has" not in ft_set:
            sources = [s for s in sources if not s.markdown_path]
        # both checked or both unchecked → no filter

        if tag_filter:
            try:
                tagged_ids = {s.id for s in db.get_sources_for_tag(int(tag_filter))}
                sources = [s for s in sources if s.id in tagged_ids]
            except (TypeError, ValueError):
                pass

        sources = _apply_sort(sources, sort_by or "id")

        try:
            psize = int(pagesize)
        except (TypeError, ValueError):
            psize = 25
        total = len(sources)
        total_pages = max(1, (total + psize - 1) // psize)
        page = (page_state or {}).get("page", 0)
        page = max(0, min(page, total_pages - 1))
        page_sources = sources[page * psize : page * psize + psize]

        peer_counts = (
            db.count_peer_reviewers(source_ids, rid, stage="full_text")
            if workflow == "independent" else {}
        )

        cards = [
            _ft_card(s, my_decisions.get(s.id), workflow, peer_counts.get(s.id, 0), db, rid)
            for s in page_sources
        ]
        if not cards:
            cards = [dbc.Alert("No sources match the current filter.", color="success")]

        prev_disabled = page <= 0
        next_disabled = page >= total_pages - 1
        page_info = f"Page {page + 1} of {total_pages}  ({total} total)" if total else ""
        n_reviewed = sum(1 for s in all_candidates if s.id in my_decisions)
        counts = f"{n_reviewed} / {len(all_candidates)} reviewed by you • {total} match current filter"
        return cards, counts, prev_disabled, next_disabled, page_info


def _ft_card(
    src: Source,
    my_decision: Optional[str],
    workflow: str,
    peer_count: int,
    db: Any,
    reviewer_id: str,
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

    # AI verdict (blinding-aware)
    ai = db.get_latest_ai_decision(sid, stage="full_text")
    ai_panel: Any = None
    if ai is not None:
        if workflow != "off" and not db.has_human_decision(sid, reviewer_id, stage="full_text"):
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
                                    ai["decision"].upper(),
                                    color=decision_color.get(ai["decision"], "secondary"),
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
    tags = db.get_tags_for_source(sid)
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
            dbc.Button(_note_label(db, sid), id={"type": "ft-note-btn", "source": sid}, size="sm", color="link", className="p-0 me-3"),
            dbc.Button("Duplicate", id={"type": "ft-duplicate", "source": sid}, size="sm", color="link", className="p-0 me-3 text-danger"),
            dbc.Button("↺ Move to screening", id={"type": "ft-move-screen", "source": sid}, size="sm", color="link", className="p-0 text-secondary"),
        ],
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
                            [left, title_el, meta_el, tag_chips_el, doi_el, read_btn, ai_panel, actions_row],
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
