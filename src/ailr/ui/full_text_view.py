"""Full-text review: card list, stage='full_text'.

Candidates = sources marked 'include' at abstract stage AND with markdown available.
Shares workflow (assisted/independent) with abstract screening.
AI's verdict at this stage is derived from extraction.flag_check.
"""

import json
import time
from typing import Any, Optional

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update

from ailr.core.config import extractors_for, team_size_for
from ailr.core.source import Source
from ailr.ui import ai_runner
from ailr.ui._actions import _apply_reset, _apply_vote
from ailr.ui._cards import (
    DECISION_COLORS,
    action_banner,
    decision_controls,
    doi_link,
    header_line,
    meta_line,
    peer_note,
    tag_chips,
)
from ailr.ui._common import triggered_click_id
from ailr.ui._project import get_project, reload_project

from ailr.ui.preprocess_view import _low_text_md
from ailr.ui.screen_view import _SORT_OPTIONS, _WITHIN_OPTIONS, _history_block

_STATUS_FILTERS = [
    {"label": "To review", "value": "to_review"},
    {"label": "Reviewed by me", "value": "reviewed"},
    {"label": "To extract", "value": "to_extract"},
    {"label": "Extracted by me", "value": "extracted_mine"},
    {"label": "Calibration sample", "value": "calibration"},
    {"label": "All", "value": "all"},
]

# Reconciliation only exists when two people extract the same paper; under `verify` the filter
# could never match, so it is not offered.
_RECONCILE_FILTER = {"label": "To reconcile", "value": "to_reconcile"}


def _status_filters(project: Any) -> list[dict]:
    if project.config.extraction.workflow != "independent":
        return _STATUS_FILTERS
    return _STATUS_FILTERS[:4] + [_RECONCILE_FILTER] + _STATUS_FILTERS[4:]




def layout() -> Any:
    project = get_project()
    return dbc.Row(
        [
            dbc.Col(
                [
                    dbc.Label("Status", className="fw-bold"),
                    dbc.RadioItems(
                        id="ft-filter-status",
                        options=_status_filters(get_project()),
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
                            {"label": "Low-text / failed", "value": "low"},
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
        Output("tabs", "data", allow_duplicate=True),
        Output("cons-store", "data", allow_duplicate=True),
        Input({"type": "ft-open-consensus", "source": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def _open_consensus(_clicks):
        trig = ctx.triggered_id
        if not isinstance(trig, dict) or not any(c.get("value") for c in (ctx.triggered or [])):
            return no_update, no_update
        return "consensus", {"sid": trig.get("source")}


    @app.callback(
        Output("ft-refresh", "data"),
        Output("ft-last-action", "data"),
        Input({"type": "ft-decide", "source": ALL, "decision": ALL}, "n_clicks"),
        Input({"type": "ft-reset", "source": ALL}, "n_clicks"),
        Input({"type": "ft-retrieval", "source": ALL, "flag": ALL}, "n_clicks"),
        State("shared-reviewer", "value"),
        prevent_initial_call=True,
    )
    def _on_action(_d, _r, _n, reviewer):
        rid = (reviewer or "").strip()
        # Act on the button that actually carries the click — not ctx.triggered_id, which can point at
        # a value-less freshly-rendered button and apply the decision to the wrong paper when a click
        # coincides with the card list re-rendering.
        triggered = triggered_click_id()
        if triggered is None or not rid:
            return no_update, no_update

        db = get_project().db

        if isinstance(triggered, dict) and triggered.get("type") == "ft-decide":
            workflow = get_project().config.screening_workflow("full_text")
            return _apply_vote(db, int(triggered["source"]), triggered["decision"], rid, workflow, stage="full_text")

        if isinstance(triggered, dict) and triggered.get("type") == "ft-reset":
            return _apply_reset(db, int(triggered["source"]), rid, stage="full_text")

        if isinstance(triggered, dict) and triggered.get("type") == "ft-retrieval":
            db.set_full_text_not_retrieved(int(triggered["source"]), bool(int(triggered["flag"])))

        return {"ts": time.time()}, no_update

    @app.callback(
        Output("ft-action-banner", "children"),
        Input("ft-last-action", "data"),
    )
    def _render_banner(last):
        return action_banner(last, undo_id="ft-banner-undo", verb="reviewed")

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
        return _apply_reset(get_project().db, int(sid), rid, stage="full_text")

    @app.callback(
        Output("ft-refresh", "data", allow_duplicate=True),
        Input({"type": "ft-duplicate", "source": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def _on_ft_mark_duplicate(_clicks):
        triggered = triggered_click_id()
        if triggered is None:
            return no_update
        get_project().db.mark_source_duplicate(int(triggered["source"]), True)
        return {"ts": time.time()}

    @app.callback(
        Output("ft-refresh", "data", allow_duplicate=True),
        Output("ft-action-banner", "children", allow_duplicate=True),
        Input({"type": "ft-reconvert", "source": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def _on_reconvert(_clicks):
        triggered = triggered_click_id()
        if triggered is None:
            return no_update, no_update
        sid = int(triggered["source"])
        from ailr.tasks.preprocess import PreprocessTask
        try:
            summary = PreprocessTask(get_project()).run(force=True, only_ids={sid})
        except Exception as e:
            return no_update, dbc.Alert(f"Re-convert #{sid} failed: {e}", color="danger", className="py-2 mb-2")
        if summary.converted:
            still_low = any(q["source_id"] == sid for q in summary.low_quality)
            msg = f"#{sid} re-converted." + (" Still low-text — the PDF is likely scanned; install and select the marker backend (OCR)." if still_low else "")
            color = "warning" if still_low else "success"
        elif summary.failed:
            err = summary.failures[0]["error"] if summary.failures else "unknown error"
            msg = f"#{sid} re-convert failed: {err}"
            color = "danger"
        else:
            msg = f"#{sid} not re-converted — no PDF found for this source."
            color = "secondary"
        return {"ts": time.time()}, dbc.Alert(msg, color=color, className="py-2 mb-2")

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
        sid = int(triggered["source"])
        db = get_project().db
        db.delete_all_screening_decisions(sid, reviewer_type="human")
        db.delete_reconciliations_for_source(sid)
        db.insert_screening_action(sid, (reviewer or "").strip() or "?", action="move_to_screening")
        return {"ts": time.time()}

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
        sid = int(data["sid"])
        # Same vote lock as the inline buttons (idempotent self-vote + team cap) — this modal
        # must not be a second, weaker path to a duplicate vote.
        workflow = get_project().config.screening_workflow("full_text")
        refresh, last = _apply_vote(get_project().db, sid, "exclude", rid, workflow, stage="full_text", reasoning=reason)
        return refresh, last, False, ""

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
        workflow = project.config.screening_workflow("full_text")

        if not rid:
            return [dbc.Alert("Enter your reviewer ID above to begin.", color="info")], "", True, True, ""

        team_size = team_size_for(workflow)
        try:
            psize = int(pagesize)
        except (TypeError, ValueError):
            psize = 25
        try:
            tag_id = int(tag_filter) if tag_filter else None
        except (TypeError, ValueError):
            tag_id = None
        low_mode, ft_avail = _ft_avail_filter(ftavail)
        # Low-text/failed is a file-content state, not a DB column: compute the id set on disk and
        # hand it to the SQL query as a whitelist (keeps filtering/paging in SQL).
        abstract_workflow = project.config.screening_workflow("abstract")
        id_whitelist = None
        if low_mode:
            threshold = project.config.preprocess.low_text_threshold
            id_whitelist = {
                cid for cid in db.full_text_candidate_ids(pid, workflow=abstract_workflow)
                if _low_text_md(project.root, cid, threshold)
            }
        # "To reconcile" is a state of the extraction records, not a screening column: resolve it to
        # an id set and hand it to the SQL query as a whitelist (same trick as low-text).
        if status == "to_reconcile":
            pending = db.sources_needing_consensus(
                list(db.full_text_candidate_ids(pid, workflow=abstract_workflow)))
            id_whitelist = pending if id_whitelist is None else (id_whitelist & pending)
            status = "all"

        req_page = (page_state or {}).get("page", 0)

        total_candidates = db.count_full_text_candidates(pid, workflow=abstract_workflow)
        if total_candidates == 0:
            return (
                [
                    dbc.Alert(
                        "No sources qualify for full-text review yet. A paper arrives here once abstract "
                        "screening is finished with it and settled on include — every reviewer the workflow "
                        "calls for has voted, and any disagreement has been adjudicated on Abstract → Conflicts. "
                        "Use the ‘Needs full-text’ filter to see included papers still awaiting their PDF/markdown.",
                        color="info",
                    )
                ],
                "0 candidates",
                True,
                True,
                "",
            )

        # Papers with an unresolved full-text conflict must be adjudicated on the FT Conflicts page
        # before they can be extracted — keep them out of the To-extract queue and off the Extract button.
        ft_conflict_ids = db.unresolved_conflict_ids(pid, workflow, stage="full_text")

        # Filter + sort + paginate in SQL: only this page's rows come back, not all candidates.
        page_sources, total, page = db.list_full_text_page(
            pid, rid, status=status, keyword=search or "", within=within or "title_and_abstract",
            tag_id=tag_id, ft_avail=ft_avail, id_whitelist=id_whitelist,
            exclude_ids=ft_conflict_ids if status == "to_extract" else None,
            team_size=team_size, extractors_required=extractors_for(project.config.extraction.workflow),
            abstract_workflow=abstract_workflow, sort_by=sort_by or "id",
            page=req_page, page_size=psize,
        )

        page_ids = [s.id for s in page_sources if s.id is not None]
        # One round-trip for all per-source scalar metadata (was six separate queries).
        meta = db.full_text_page_meta(page_ids, rid, stage="full_text", team_size=team_size)
        my_decisions = meta["my_decisions"]
        peer_counts = meta["peer_counts"] if workflow == "independent" else {}
        extract_ids = meta["extract_eligible"] - ft_conflict_ids  # extraction-eligible, minus unresolved conflicts
        extracted_by = meta["extracted_by"]                       # {sid: extractor_id who submitted}
        claimed_by = meta["claimed_by"]                           # {sid: extractor_id holding it, draft included}
        ai_by_source = meta["ai_decisions"]
        note_counts = meta["note_counts"]
        tags_by_source = db.get_tags_for_sources(page_ids)        # one-to-many, kept as its own query
        from ailr.ui.ai_runner import current_extraction_composed
        stale_ids = db.stale_ai_extraction_source_ids(pid, current_extraction_composed(project))
        reconcile_ids = (
            db.sources_needing_consensus(page_ids)
            if project.config.extraction.workflow == "independent" else set()
        )
        companions_by_source = db.list_study_companions(page_ids)

        cards = [
            _ft_card(
                s, my_decisions.get(s.id), workflow, peer_counts.get(s.id, 0), rid,
                can_extract=s.id in extract_ids, expand_abstract=bool(expand_all),
                extracted_by=extracted_by.get(s.id),
                claimed_by=claimed_by.get(s.id),
                extract_verify=project.config.extraction.workflow == "verify",
                low_text=_low_text_md(project.root, s.id, project.config.preprocess.low_text_threshold),
                tags=tags_by_source.get(s.id, []),
                ai_decision=ai_by_source.get(s.id),
                note_count=note_counts.get(s.id, 0),
                stale=s.id in stale_ids,
                needs_reconcile=s.id in reconcile_ids,
                companions=companions_by_source.get(s.id, []),
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
        if stale_ids:
            counts += f" • {len(stale_ids)} AI extraction(s) outdated — re-run extraction"
        return cards, counts, prev_disabled, next_disabled, page_info


def _ft_avail_filter(ftavail: Any) -> tuple[bool, Optional[str]]:
    """(low_text_mode, ft_avail) from the 'Full-text' checklist.

    'Low-text / failed' wins over the other two: it is resolved on disk into an id whitelist, so the
    SQL-level has/needs filter is switched off to avoid narrowing that set twice. Ticking both
    'has' and 'needs' (or neither) means no availability filter at all.
    """
    ft_set = set(ftavail or [])
    if "low" in ft_set:
        return True, None
    if "has" in ft_set and "needs" not in ft_set:
        return False, "has"
    if "needs" in ft_set and "has" not in ft_set:
        return False, "needs"
    return False, None




def _ft_card(
    src: Source,
    my_decision: Optional[str],
    workflow: str,
    peer_count: int,
    reviewer_id: str,
    can_extract: bool = False,
    expand_abstract: bool = False,
    extracted_by: Optional[str] = None,
    claimed_by: Optional[str] = None,
    extract_verify: bool = False,
    low_text: bool = False,
    tags: Optional[list[dict]] = None,
    ai_decision: Optional[str] = None,
    note_count: int = 0,
    stale: bool = False,
    needs_reconcile: bool = False,
    companions: Optional[list[dict]] = None,
) -> Any:
    sid = src.id
    # Excluding at full text goes through a modal first: PRISMA wants the reason.
    right = decision_controls(sid, my_decision, prefix="ft", exclude_id={"type": "ft-exclude-open", "source": sid})
    peer_indicator = peer_note(workflow, peer_count)

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
                                    color=DECISION_COLORS.get(ai_decision, "secondary"),
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

    doi_el = doi_link(src)
    tag_chips_el = tag_chips(tags)

    # Duplicate and 'Same study' sit next to each other because they are the two record-to-record
    # actions, and they are the pair most easily confused — hence the tooltips contrasting them.
    actions_row = html.Div(
        [
            dbc.Button("History", id={"type": "ft-history-btn", "source": sid}, size="sm", color="link", className="p-0 me-3"),
            dbc.Button("Tags", id={"type": "ft-tag-btn", "source": sid}, size="sm", color="link", className="p-0 me-3"),
            dbc.Button(f"Note ({note_count})" if note_count else "Note", id={"type": "ft-note-btn", "source": sid}, size="sm", color="link", className="p-0 me-3"),
            dbc.Button("Duplicate", id={"type": "ft-duplicate", "source": sid}, size="sm", color="link", className="p-0 me-3 text-danger"),
            dbc.Tooltip(
                "The same record imported twice. It leaves the review and is counted under "
                "‘duplicates removed’. For a different paper reporting the same study, use "
                "‘Same study as…’ instead.",
                target={"type": "ft-duplicate", "source": sid}, placement="bottom",
            ),
            dbc.Button(
                "Change study grouping" if companions else "Same study as…",
                id={"type": "ft-study-btn", "source": sid},
                size="sm", color="link", className="p-0 me-3 text-secondary",
            ),
            dbc.Tooltip(
                "Several reports of one study — main paper, protocol, secondary analysis — count as "
                "one study in the PRISMA flow. All of them stay in the review; only the study count "
                "changes. Not the same as Duplicate.",
                target={"type": "ft-study-btn", "source": sid}, placement="bottom",
            ),
            dbc.Button("↺ Move to screening", id={"type": "ft-move-screen", "source": sid}, size="sm", color="link", className="p-0 me-3 text-secondary"),
            dbc.Tooltip(
                "Sends this paper back to abstract screening: your full-text and abstract decisions "
                "on it are cleared so it can be reviewed again.",
                target={"type": "ft-move-screen", "source": sid}, placement="bottom",
            ),
            dbc.Button("⟳ Re-convert PDF", id={"type": "ft-reconvert", "source": sid}, size="sm", color="link", className="p-0 text-secondary"),
            dbc.Tooltip(
                "Runs the PDF through conversion again with the backend currently selected under "
                "Full text → Workflow → Preparation. Use it when the extracted text looks short or garbled.",
                target={"type": "ft-reconvert", "source": sid}, placement="bottom",
            ),
        ],
        className="mt-1",
    )

    extract_row: Any = None
    if needs_reconcile:
        extract_row = html.Div(
            [
                dbc.Button(
                    "Open comparison →",
                    id={"type": "ft-open-consensus", "source": sid},
                    size="sm", color="warning", outline=True, className="me-2",
                ),
                dbc.Badge("Two extractions — needs reconciling", color="warning", className="align-middle"),
            ],
            className="mt-2",
        )
    elif can_extract:
        # A saved draft claims a paper under `verify` just as a submission does, so the queue has to
        # show it — otherwise a paper someone is mid-way through still reads "To extract".
        drafted_by = claimed_by if extracted_by is None and extract_verify else None
        locked = (
            (extracted_by is not None and extracted_by != reviewer_id)
            or (drafted_by is not None and drafted_by != reviewer_id)
        ) and extract_verify
        if drafted_by is not None:
            status_badge = dbc.Badge(
                "Your draft — in progress" if drafted_by == reviewer_id else f"In progress by {drafted_by}",
                color="info", className="align-middle",
            )
        elif extracted_by is None:
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
        if stale:
            children.append(
                dbc.Badge("AI extraction outdated — re-run", color="warning", className="align-middle ms-2",
                          title="Criteria or the extraction prompt changed since this paper was AI-extracted."),
            )
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

    # State only; the button that sets it lives in actions_row with the other per-record actions.
    study_el = html.Div(
        dbc.Badge(
            f"Same study as {', '.join('#' + str(c['id']) for c in companions)}",
            color="info", className="me-2",
        ),
        className="mt-1",
    ) if companions else None

    # PRISMA counts a report as "not retrieved" only once a human says the full text could not be
    # obtained; until then a paper without markdown is just work outstanding.
    retrieval_el: Any = None
    if src.full_text_not_retrieved:
        retrieval_el = html.Div(
            [
                dbc.Badge("Full text not retrieved", color="dark", className="me-2"),
                dbc.Button("Undo", id={"type": "ft-retrieval", "source": sid, "flag": 0},
                           size="sm", color="link", className="p-0 text-decoration-none"),
            ],
            className="mt-1",
        )
    elif not src.markdown_path:
        retrieval_el = html.Div(
            dbc.Button("Mark full text as not retrieved", id={"type": "ft-retrieval", "source": sid, "flag": 1},
                       size="sm", color="link", className="p-0 text-decoration-none text-muted",
                       title="Counts this report under PRISMA's 'reports not retrieved'."),
            className="mt-1",
        )

    left = header_line(src)
    title_el = html.H6(src.title, className="mb-1")
    meta_el = meta_line(src)

    return dbc.Card(
        dbc.CardBody(
            [
                dbc.Row(
                    [
                        dbc.Col(
                            [left, title_el, meta_el, low_text_badge, study_el, retrieval_el, abstract_block, tag_chips_el, doi_el, read_btn, ai_panel, actions_row, extract_row],
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
