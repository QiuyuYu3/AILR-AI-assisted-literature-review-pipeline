"""Screening review: card list with inline decision buttons."""

import json
import time
from pathlib import Path
from typing import Any, Optional

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update

from ailr.core.source import Source
from ailr.extraction import compose_screening_prompt
from ailr.reviewers import ScreeningDecision
from ailr.ui import ai_runner, version_ui
from ailr.ui._common import (
    _short_author_year,
    get_project,
    help_icon,
    read_criteria,
    read_screening_additional,
    read_screening_prompt,
    triggered_click_id,
    with_help,
)


def _screen_prompt_text() -> str:
    return read_screening_prompt()


def _screen_additional_text() -> str:
    return read_screening_additional()



def _screening_run_prompt() -> str:
    """A ready-to-paste prompt for running AI screening externally, with the exact output format."""
    criteria = read_criteria()
    return (
        "You are screening study abstracts for a literature review. For EACH abstract, decide "
        "include / exclude / uncertain against the criteria below.\n\n"
        "=== INCLUSION / EXCLUSION CRITERIA ===\n" + criteria + "\n\n"
        "=== OUTPUT — return ONLY a JSON array, one object per abstract ===\n"
        "[\n"
        "  {\n"
        '    "source_id": <the id shown with the abstract>,\n'
        '    "decision": "include" | "exclude" | "uncertain",\n'
        '    "reasoning": "1-2 sentences",\n'
        '    "confidence": 1-10,\n'
        '    "matched_criteria": ["criterion ids, optional"],\n'
        '    "evidence_quotes": ["short verbatim quotes, optional"]\n'
        "  }\n"
        "]\n\n"
        "=== ABSTRACTS (each begins with its source_id) ===\n"
        "<paste abstracts here, e.g.  'source_id 12: <title> — <abstract>'>"
    )


def _prompt_version_options() -> list[Any]:
    project = get_project()
    vers = project.db.list_prompt_versions(project.project_id, "screening")
    return [
        {"label": f"{v['version']} • {v['created_at']}" + (f" • {v['notes']}" if v.get("notes") else ""),
         "value": v["version"]}
        for v in vers
    ]


_STATUS_FILTERS = [
    {"label": "To screen", "value": "to_screen"},
    {"label": "Reviewed by me", "value": "reviewed"},
    {"label": "Calibration sample", "value": "calibration"},
    {"label": "All", "value": "all"},
]

_SORT_OPTIONS = [
    {"label": "ID", "value": "id"},
    {"label": "Author", "value": "author"},
    {"label": "Title", "value": "title"},
    {"label": "Year (newest)", "value": "year_desc"},
    {"label": "Year (oldest)", "value": "year_asc"},
]

_PAGE_SIZES = [
    {"label": "25 per page", "value": "25"},
    {"label": "50 per page", "value": "50"},
    {"label": "100 per page", "value": "100"},
]

_WITHIN_OPTIONS = [
    {"label": "Title and abstract", "value": "title_and_abstract"},
    {"label": "Authors", "value": "authors"},
    {"label": "All fields (incl. DOI)", "value": "all"},
]


def screening_prompt_panel() -> list[Any]:
    """Screening prompt editing (prompt + additional instructions + preview + versions).
    Rendered on the abstract Prompt tab."""
    return [
        dbc.Label("Screening prompt", className="fw-bold"),
        dbc.Alert(
            [
                html.Strong("You usually only edit the additional instructions below. "),
                "The criteria are shared with extraction and edited on the Protocol page; the output "
                "(decision / reasoning / confidence / matched_criteria / quotes) is enforced automatically. "
                "The full screening prompt is a ready-made template you rarely need to touch (see Advanced).",
            ],
            color="light", className="small py-2",
        ),
        with_help(
            dbc.Label("Additional instructions (optional)", className="fw-bold mb-0 me-1"),
            "Free-form guidance appended to the screening prompt — e.g. at the abstract stage be "
            "lenient and exclude only on clear violations, leaving borderline cases for full text. "
            "The criteria stay the same across stages; stage-specific judgement goes here.",
            "screen-additional-help",
            className="mt-0",
        ),
        dbc.Textarea(id="screen-additional", value=_screen_additional_text(), style={"height": "120px", "fontFamily": "monospace", "fontSize": "0.75rem"}),
        dbc.Button("Save additional instructions", id="screen-additional-save", color="primary", size="sm", className="mt-1"),
        html.Div(id="screen-additional-feedback", className="small mt-1"),
        with_help(
            html.H6("Full prompt preview", className="mb-0 me-1"),
            "The exact prompt sent to the AI, with your criteria and additional instructions filled in.",
            "screen-preview-help",
        ),
        html.Div(id="screen-prompt-composed"),
        html.Div(
            [
                with_help(
                    html.H6("Version history", className="mb-0 me-1"),
                    "A version is saved automatically when you run AI screening (only if the prompt, criteria, or additional instructions changed). AI decisions are tagged with it, and the full resolved prompt is stored for reproducibility.",
                    "screen-ver-help",
                ),
                html.Div(id="screen-prompt-ver-feedback", className="small mb-1"),
                dbc.InputGroup(
                    [
                        dbc.Select(id="screen-prompt-ver-select", options=_prompt_version_options(), size="sm"),
                        dbc.Button("Restore to editor", id="screen-prompt-ver-restore", color="secondary", outline=True, size="sm"),
                    ],
                    className="mb-1",
                ),
                html.Div(id="screen-prompt-ver-view", className="small text-muted"),
                dbc.Label("Compare the selected version with", className="small fw-bold mb-0 mt-2"),
                dbc.Select(id="screen-prompt-ver-b", options=_prompt_version_options(), size="sm", className="mb-1"),
                html.Div(id="screen-prompt-ver-diff"),
            ],
            className="mt-3",
        ),
        html.Details(
            [
                html.Summary("Advanced: edit the full screening prompt"),
                html.Div(
                    [
                        dbc.Alert(
                            [
                                html.Strong("Most users don't need this. "),
                                "This is the fixed template the parts above plug into. Keep the markers ",
                                html.Code("{{criteria}}"), " and ", html.Code("{{additional}}"),
                                " so ailr can fill them in. The output format is enforced separately.",
                            ],
                            color="light", className="small py-2 mt-2",
                        ),
                        dbc.Textarea(id="screen-prompt", value=_screen_prompt_text(), style={"height": "220px", "fontFamily": "monospace", "fontSize": "0.75rem"}),
                        dbc.Button("Save prompt", id="screen-prompt-save", color="primary", size="sm", className="mt-1"),
                        html.Div(id="screen-prompt-feedback", className="small mt-1"),
                    ],
                    className="ps-2",
                ),
            ],
            className="mt-3",
        ),
    ]


def ai_screening_panel() -> list[Any]:
    """Run AI screening + import externally-run results. Rendered on the abstract AI screening tab."""
    return [
        dbc.Label("Run AI screening", className="fw-bold"),
        html.P("Runs AI on the abstracts and records its decisions (the prompt is snapshotted as a version).", className="text-muted small mb-1"),
        dbc.Switch(id="screen-ai-mock", label="Mock (no API cost)", value=True, className="small"),
        dbc.Button("Run AI screening", id="screen-ai-run", color="primary", outline=True, size="sm"),
        html.Div(id="screen-ai-status", className="small mt-2"),
        dcc.Interval(id="screen-ai-poll", interval=1200, disabled=True),
        dcc.ConfirmDialogProvider(
            dbc.Button("Clear mock AI results", color="link", size="sm", className="text-danger p-0 mt-2"),
            id="screen-clear-mock",
            message="Delete all MOCK AI screening decisions in this project? Real AI and human decisions are kept.",
        ),
        html.Div(id="screen-clear-mock-status", className="small mt-1"),
        html.Hr(className="my-3"),
        html.Details(
            [
                html.Summary("Import AI results run elsewhere (optional)", className="fw-bold"),
                html.P("Only needed if you ran the AI outside ailr (e.g. ChatGPT/Claude). Path to a .json (list / one record) or a FOLDER of per-paper .json. Keys are fixed reserved names — anything else is ignored.", className="text-muted small mb-1 mt-2"),
                html.Ul(
                    [
                        html.Li([html.Code("source_id"), " or ", html.Code("doi"), " — which paper (else the filename is used)."], className="small"),
                        html.Li([html.Code("decision"), " — required, must be ", html.Code("include / exclude / uncertain"), "."], className="small"),
                        html.Li([html.Code("reasoning"), ", ", html.Code("confidence"), ", ", html.Code("matched_criteria"), ", ", html.Code("evidence_quotes"), " — optional."], className="small"),
                    ],
                    className="mb-1",
                ),
                html.Details(
                    [
                        html.Summary("Example record", className="small"),
                        html.Pre('{"source_id": 12, "decision": "include", "confidence": 0.9,\n "reasoning": "dyadic interaction study", "matched_criteria": ["C1"]}',
                                 className="small", style={"fontSize": "0.72rem"}),
                    ],
                    className="mb-1",
                ),
                html.Details(
                    [
                        html.Summary("Run externally — copy prompt / download template", className="small"),
                        html.Div(
                            [
                                dcc.Clipboard(target_id="screen-runprompt", title="Copy prompt", style={"display": "inline-block", "marginRight": "6px", "cursor": "pointer"}),
                                html.Span("Copy → paste into ChatGPT/Claude with your abstracts → paste the JSON it returns into the box below.", className="text-muted small"),
                            ],
                            className="mb-1",
                        ),
                        dbc.Textarea(id="screen-runprompt", value=_screening_run_prompt(), style={"height": "140px", "fontFamily": "monospace", "fontSize": "0.68rem"}),
                        dbc.Button("Download JSON template (per paper)", id="screen-import-template-btn", color="link", size="sm", className="p-0 mt-1"),
                        dcc.Download(id="screen-import-template-dl"),
                    ],
                    className="mt-1",
                ),
                dbc.Input(id="screen-importai-path", placeholder="C:/path/to/screen_results.json or folder", size="sm", className="mb-1 mt-2"),
                dbc.Button("Import", id="screen-importai-run", color="secondary", outline=True, size="sm"),
                html.Div(id="screen-importai-status", className="small mt-1"),
            ],
            className="mt-2",
        ),
    ]


def layout() -> Any:
    project = get_project()
    return dbc.Row(
        [
            dbc.Col(
                [
                    dbc.Label("Status", className="fw-bold"),
                    dbc.RadioItems(
                        id="screen-filter-status",
                        options=_STATUS_FILTERS,
                        value="to_screen",
                    ),

                    html.Hr(),

                    dbc.Label("Sort", className="fw-bold"),
                    dbc.Select(id="screen-sort", options=_SORT_OPTIONS, value="id"),

                    dbc.Label("Display", className="fw-bold mt-2"),
                    dbc.Select(id="screen-pagesize", options=_PAGE_SIZES, value="25"),

                    dbc.Switch(
                        id="screen-expand-all",
                        label="Expand all abstracts",
                        value=True,
                        className="mt-2",
                        label_class_name="fw-bold",
                    ),

                    html.Hr(),

                    dbc.Label("Filter", className="fw-bold"),

                    dbc.Label("Tags", className="small mt-1"),
                    dbc.Select(
                        id="screen-tags-filter",
                        options=[{"label": "(any)", "value": ""}],
                        value="",
                        className="mb-2",
                    ),

                    dbc.Label("Keyword search", className="small"),
                    dbc.Input(
                        id="screen-search",
                        placeholder="Type and press Enter",
                        debounce=True,
                        className="mb-2",
                    ),

                    dbc.Label("Within", className="small"),
                    dbc.RadioItems(
                        id="screen-within",
                        options=_WITHIN_OPTIONS,
                        value="title_and_abstract",
                        className="mb-2",
                    ),

                    dbc.Button(
                        "↻ Reset filters",
                        id="screen-reset-filters",
                        color="secondary",
                        outline=True,
                        size="sm",
                        className="w-100",
                    ),

                    html.Hr(),

                    html.Div(id="screen-counts", className="small text-muted"),
                ],
                width=3,
            ),
            dbc.Col(
                [
                    html.Div(id="screen-action-banner"),
                    html.Div(id="screen-cards"),
                    html.Div(
                        [
                            dbc.Button(
                                "← Prev",
                                id="screen-page-prev",
                                disabled=True,
                                color="secondary",
                                outline=True,
                                size="sm",
                                className="me-2",
                            ),
                            html.Span(id="screen-page-info", className="text-muted small"),
                            dbc.Button(
                                "Next →",
                                id="screen-page-next",
                                disabled=True,
                                color="secondary",
                                outline=True,
                                size="sm",
                                className="ms-2",
                            ),
                        ],
                        id="screen-pagination",
                        className="d-flex justify-content-center align-items-center mt-3",
                    ),
                ],
                width=9,
            ),
        ]
    )


def register_callbacks(app: Any) -> None:
    @app.callback(
        Output("screen-ai-poll", "disabled"),
        Output("screen-ai-status", "children"),
        Input("screen-ai-run", "n_clicks"),
        State("screen-ai-mock", "value"),
        prevent_initial_call=True,
    )
    def _ai_run(n, mock):
        if not n:
            return no_update, no_update
        started = ai_runner.start_screening(get_project(), bool(mock))
        msg = "AI screening started…" if started else "Already running…"
        return False, dbc.Alert(msg, color="info", className="py-1 mb-0")

    @app.callback(
        Output("screen-clear-mock-status", "children"),
        Output("screen-refresh", "data", allow_duplicate=True),
        Input("screen-clear-mock", "submit_n_clicks"),
        prevent_initial_call=True,
    )
    def _clear_mock(n):
        if not n:
            return no_update, no_update
        import time as _t
        project = get_project()
        cleared = project.db.clear_mock_ai_decisions(project.project_id)
        return dbc.Alert(f"Cleared {cleared} mock AI screening decision(s).", color="success", className="py-1 mb-0"), {"ts": _t.time()}

    @app.callback(
        Output("screen-ai-status", "children", allow_duplicate=True),
        Output("screen-ai-poll", "disabled", allow_duplicate=True),
        Output("screen-refresh", "data", allow_duplicate=True),
        Output("screen-prompt-ver-select", "options", allow_duplicate=True),
        Input("screen-ai-poll", "n_intervals"),
        prevent_initial_call=True,
    )
    def _ai_poll(_n):
        st = ai_runner.get_status("screening")
        if st.get("running"):
            done, total = st.get("done", 0), st.get("total", 0)
            pct = int(done / total * 100) if total else 0
            bar = dbc.Progress(value=pct, label=f"{done}/{total}", striped=True, animated=True, className="mt-1")
            return html.Div(["Running AI screening…", bar]), False, no_update, no_update
        if st.get("error"):
            return dbc.Alert(f"AI screening failed: {st['error']}", color="danger", className="py-1 mb-0"), True, no_update, no_update
        if st.get("started") and st.get("summary"):
            # A run may have just snapshotted a new prompt version — refresh the dropdown.
            return dbc.Alert(st["summary"], color="success", className="py-1 mb-0"), True, {"ts": time.time()}, _prompt_version_options()
        return no_update, True, no_update, no_update

    @app.callback(
        Output("screen-importai-status", "children"),
        Output("screen-refresh", "data", allow_duplicate=True),
        Input("screen-importai-run", "n_clicks"),
        State("screen-importai-path", "value"),
        prevent_initial_call=True,
    )
    def _import_ai_screening(n, path):
        if not n:
            return no_update, no_update
        p = Path((path or "").strip())
        if not path or not p.exists():
            return dbc.Alert("Enter a valid file or folder path.", color="warning", className="py-1 mb-0"), no_update
        files = [p] if p.is_file() else sorted(p.glob("*.json"))
        if not files:
            return dbc.Alert("No .json file(s) found.", color="warning", className="py-1 mb-0"), no_update

        records: list = []
        errors: list = []
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

        from ailr.ingest.results_import import import_ai_screening_results

        s = import_ai_screening_results(get_project(), records, stage="abstract")
        msg = f"Imported {s.imported}/{s.total_records}; {len(s.unmatched)} unmatched, {len(s.errors) + len(errors)} error(s)."
        return dbc.Alert(msg, color="success", className="py-1 mb-0"), {"ts": time.time()}

    @app.callback(
        Output("screen-import-template-dl", "data"),
        Input("screen-import-template-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def _download_screen_template(n):
        if not n:
            return no_update
        project = get_project()
        recs = [
            {"source_id": s.id, "_title": s.title, "decision": "", "reasoning": "",
             "confidence": None, "matched_criteria": [], "evidence_quotes": []}
            for s in project.db.list_sources(project.project_id)
        ]
        return dict(content=json.dumps(recs, indent=2, ensure_ascii=False), filename="screening_import_template.json")

    @app.callback(
        Output("screen-prompt-feedback", "children"),
        Input("screen-prompt-save", "n_clicks"),
        State("screen-prompt", "value"),
        prevent_initial_call=True,
    )
    def _save_screen_prompt(n, text):
        if not n:
            return no_update
        project = get_project()
        p = project.root / project.config.screening.prompt
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text or "", encoding="utf-8")
        except OSError as e:
            return dbc.Alert(f"Save failed: {e}", color="danger", className="mb-0 py-1")
        return dbc.Alert(f"Saved to {p.name}.", color="success", className="mb-0 py-1")

    @app.callback(
        Output("screen-prompt", "value", allow_duplicate=True),
        Output("screen-prompt-ver-feedback", "children", allow_duplicate=True),
        Input("screen-prompt-ver-restore", "n_clicks"),
        State("screen-prompt-ver-select", "value"),
        prevent_initial_call=True,
    )
    def _restore_prompt_version(n, version):
        if not n or not version:
            return no_update, no_update
        project = get_project()
        v = project.db.get_prompt_version(project.project_id, "screening", version)
        if not v:
            return no_update, dbc.Alert("Version not found.", color="warning", className="mb-0 py-1")
        return v["content"], dbc.Alert(f"Loaded {version} into the editor. Click ‘Save prompt’ to write it to the file.", color="info", className="mb-0 py-1")

    @app.callback(
        Output("screen-prompt-ver-view", "children"),
        Input("screen-prompt-ver-select", "value"),
    )
    def _view_prompt_version(version):
        if not version:
            return ""
        project = get_project()
        v = project.db.get_prompt_version(project.project_id, "screening", version)
        if not v:
            return ""
        bits = [f"{version} · {v['created_at']}"]
        if v.get("notes"):
            bits.append(v["notes"])
        header = " — ".join(bits)
        composed = v.get("composed")
        if not composed:
            return header
        return html.Div(
            [
                html.Div(header),
                html.Details(
                    [
                        html.Summary("Exact prompt sent (criteria + additional resolved)", className="small"),
                        html.Pre(composed, style={"whiteSpace": "pre-wrap", "fontSize": "0.72rem", "maxHeight": "300px", "overflow": "auto"}),
                    ],
                    className="mt-1",
                ),
            ]
        )

    @app.callback(
        Output("screen-prompt-ver-diff", "children"),
        Input("screen-prompt-ver-select", "value"),
        Input("screen-prompt-ver-b", "value"),
    )
    def _diff_screening_prompt(va, vb):
        if not va or not vb:
            return html.Small("Pick two versions to compare.", className="text-muted")
        project = get_project()
        a = project.db.get_prompt_version(project.project_id, "screening", va) or {}
        b = project.db.get_prompt_version(project.project_id, "screening", vb) or {}
        return version_ui.diff_view(a.get("composed") or "", b.get("composed") or "")

    @app.callback(
        Output("screen-additional-feedback", "children"),
        Input("screen-additional-save", "n_clicks"),
        State("screen-additional", "value"),
        prevent_initial_call=True,
    )
    def _save_screen_additional(n, text):
        if not n:
            return no_update
        project = get_project()
        p = project.root / project.config.screening.additional
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text or "", encoding="utf-8")
        except OSError as e:
            return dbc.Alert(f"Save failed: {e}", color="danger", className="mb-0 py-1")
        return dbc.Alert(f"Saved to {p.name}.", color="success", className="mb-0 py-1")

    @app.callback(
        Output("screen-prompt-composed", "children"),
        Input("screen-prompt", "value"),
        Input("screen-additional", "value"),
    )
    def _composed_screen_prompt(text, additional):
        composed = compose_screening_prompt(text, criteria=read_criteria(), additional=additional or "")
        return html.Pre(
            composed + "\n\n--- [THE ABSTRACT IS APPENDED HERE AUTOMATICALLY] ---",
            style={"whiteSpace": "pre-wrap", "fontSize": "0.72rem", "maxHeight": "300px", "overflow": "auto"},
        )

    @app.callback(
        Output("screen-search", "value"),
        Output("screen-within", "value"),
        Output("screen-filter-status", "value"),
        Input("screen-reset-filters", "n_clicks"),
        prevent_initial_call=True,
    )
    def _reset_filters(_clicks):
        return "", "title_and_abstract", "to_screen"

    @app.callback(
        Output("screen-tags-filter", "options"),
        Input("tabs", "data"),
        Input("tags-refresh", "data"),
    )
    def _populate_tag_options(tab, _refresh):
        if tab != "screen":
            return no_update
        project = get_project()
        tags = project.db.list_tags(project.project_id)
        opts = [{"label": "(any)", "value": ""}]
        opts.extend({"label": t["name"], "value": str(t["id"])} for t in tags)
        return opts

    @app.callback(
        Output("screen-page", "data"),
        Input("screen-page-prev", "n_clicks"),
        Input("screen-page-next", "n_clicks"),
        Input("screen-filter-status", "value"),
        Input("screen-search", "value"),
        Input("screen-within", "value"),
        Input("screen-tags-filter", "value"),
        Input("screen-pagesize", "value"),
        Input("screen-sort", "value"),
        Input("shared-reviewer", "value"),
        State("screen-page", "data"),
        prevent_initial_call=True,
    )
    def _page_nav(prev, nxt, _f, _s, _w, _tg, _ps, _sort, _rev, current):
        trigger = ctx.triggered_id
        page = (current or {}).get("page", 0)
        if trigger == "screen-page-prev":
            page = max(0, page - 1)
        elif trigger == "screen-page-next":
            page = page + 1
        else:
            page = 0
        return {"page": page}

    @app.callback(
        Output("screen-refresh", "data"),
        Output("screen-last-action", "data"),
        Input({"type": "screen-decide", "source": ALL, "decision": ALL}, "n_clicks"),
        Input({"type": "screen-reset", "source": ALL}, "n_clicks"),
        State("shared-reviewer", "value"),
        prevent_initial_call=True,
    )
    def _on_action(_decide_clicks, _reset_clicks, reviewer):
        rid = (reviewer or "").strip()
        # Act on the button that actually carries the click — not ctx.triggered_id, which can point at
        # a value-less freshly-rendered button and apply the decision to the wrong paper when a click
        # coincides with the card list re-rendering.
        triggered = triggered_click_id()
        if triggered is None or not rid:
            return no_update, no_update

        db = get_project().db

        if isinstance(triggered, dict) and triggered.get("type") == "screen-decide":
            source_id = int(triggered["source"])
            decision = triggered["decision"]
            # Vote lock in one query: skip if I already decided this paper (rapid double-click), and
            # cap the team size — 1 human (+ AI) in assisted, 2 humans in independent.
            i_voted, others = db.screening_lock_check(source_id, rid, "abstract")
            if i_voted:
                return {"ts": time.time()}, no_update
            team_humans = 1 if get_project().config.screening.workflow == "assisted" else 2
            if others >= team_humans:
                other = db.other_human_decided(source_id, "abstract", rid) or "another reviewer"
                return {"ts": time.time()}, {"blocked": True, "by": other, "sid": source_id, "ts": time.time()}
            with db._conn.transaction():  # decision + action in one commit
                db.insert_screening_decision(
                    ScreeningDecision(
                        decision=decision,
                        reasoning="(inline screening)",
                        reviewer_type="human",
                        reviewer_id=rid,
                        source_id=source_id,
                    )
                )
                db.insert_screening_action(source_id, rid, action="vote", decision=decision)
            src = db.get_source(source_id)
            return {"ts": time.time()}, {
                "sid": source_id,
                "decision": decision,
                "author_year": _short_author_year(src) if src else "",
                "title": src.title if src else "",
                "ts": time.time(),
            }

        if isinstance(triggered, dict) and triggered.get("type") == "screen-reset":
            source_id = int(triggered["source"])
            db.delete_screening_decision(source_id, rid, reviewer_type="human")
            db.delete_reconciliations_for_source(source_id, "abstract_screening")
            db.insert_screening_action(source_id, rid, action="reset")
            return {"ts": time.time()}, None  # clear banner

        return {"ts": time.time()}, no_update

    @app.callback(
        Output("screen-action-banner", "children"),
        Input("screen-last-action", "data"),
    )
    def _render_banner(last):
        if not last or not isinstance(last, dict):
            return ""
        if last.get("blocked"):
            return dbc.Alert(
                [
                    html.Span(f"#{last.get('sid')} was already screened by ", className="me-1"),
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
                dbc.Button(
                    "Undo",
                    id="screen-banner-undo",
                    color="link",
                    size="sm",
                    className="p-0",
                ),
            ],
            color="light",
            className="py-2 mb-2 d-flex align-items-center flex-wrap",
        )

    @app.callback(
        Output("screen-refresh", "data", allow_duplicate=True),
        Input({"type": "screen-duplicate", "source": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def _on_mark_duplicate(_clicks):
        triggered = triggered_click_id()
        if triggered is None:
            return no_update
        get_project().db.mark_source_duplicate(int(triggered["source"]), True)
        return {"ts": time.time()}

    @app.callback(
        Output("screen-refresh", "data", allow_duplicate=True),
        Output("screen-last-action", "data", allow_duplicate=True),
        Input("screen-banner-undo", "n_clicks"),
        State("screen-last-action", "data"),
        State("shared-reviewer", "value"),
        prevent_initial_call=True,
    )
    def _on_banner_undo(_clicks, last, reviewer):
        if not _clicks:  # ignore the auto-fire when the banner (and its Undo button) is re-created
            return no_update, no_update
        if not last or not isinstance(last, dict):
            return no_update, no_update
        rid = (reviewer or "").strip()
        if not rid:
            return no_update, no_update
        sid = last.get("sid")
        if sid is None:
            return no_update, no_update
        db = get_project().db
        db.delete_screening_decision(int(sid), rid, reviewer_type="human")
        db.delete_reconciliations_for_source(int(sid), "abstract_screening")
        db.insert_screening_action(int(sid), rid, action="reset")
        return {"ts": time.time()}, None

    @app.callback(
        Output({"type": "screen-abstract-body", "source": ALL}, "is_open"),
        Input({"type": "screen-abstract-btn", "source": ALL}, "n_clicks"),
        State({"type": "screen-abstract-body", "source": ALL}, "is_open"),
        prevent_initial_call=True,
    )
    def _toggle_abstract(clicks, is_open_list):
        from dash import callback_context
        triggered = ctx.triggered_id
        if triggered is None:
            return no_update
        target_sid = triggered.get("source")
        ids = [t["id"] for t in callback_context.inputs_list[0]]
        out = []
        for i, comp_id in enumerate(ids):
            if comp_id.get("source") == target_sid:
                out.append(not is_open_list[i])
            else:
                out.append(is_open_list[i])
        return out


    @app.callback(
        Output("screen-cards", "children"),
        Output("screen-page-prev", "disabled"),
        Output("screen-page-next", "disabled"),
        Output("screen-page-info", "children"),
        Output("screen-counts", "children"),
        Input("screen-filter-status", "value"),
        Input("screen-search", "value"),
        Input("screen-within", "value"),
        Input("screen-tags-filter", "value"),
        Input("screen-pagesize", "value"),
        Input("screen-sort", "value"),
        Input("screen-page", "data"),
        Input("screen-refresh", "data"),
        Input("shared-reviewer", "value"),
        Input("screen-expand-all", "value"),
        Input("tags-refresh", "data"),
        Input("notes-refresh", "data"),
    )
    def _render(status, search, within, tag_filter, pagesize, sort_by, page_state, _refresh, reviewer, expand_all, _tr, _nr):
        project = get_project()
        db = project.db
        pid = project.project_id
        rid = (reviewer or "").strip()
        workflow = project.config.screening.workflow

        if not rid:
            empty = dbc.Alert("Enter your reviewer ID above to begin.", color="info")
            return empty, True, True, "", ""

        # Team-aware "To screen": independent = 2 humans per paper, assisted = 1 human (+ AI).
        team_size = 2 if workflow == "independent" else 1
        try:
            psize = int(pagesize)
        except (TypeError, ValueError):
            psize = 25
        try:
            tag_id = int(tag_filter) if tag_filter else None
        except (TypeError, ValueError):
            tag_id = None
        req_page = (page_state or {}).get("page", 0)

        # Filter + sort + paginate in SQL: only this page's rows come back, not the whole table.
        page_sources, total, page = db.list_sources_page(
            pid, rid, stage="abstract", status=status, keyword=search or "",
            within=within or "title_and_abstract", tag_id=tag_id, team_size=team_size,
            sort_by=sort_by, page=req_page, page_size=psize,
        )

        visible_ids = [s.id for s in page_sources if s.id is not None]
        my_decisions = db.get_decisions_by_reviewer(visible_ids, rid)
        peer_counts = db.count_peer_reviewers(visible_ids, rid) if workflow == "independent" else {}
        tags_per_source = db.get_tags_for_sources(visible_ids)
        note_counts = db.count_notes(visible_ids)

        cards = [
            _source_card(
                s, my_decisions.get(s.id), workflow, peer_counts.get(s.id, 0),
                bool(expand_all), rid,
                tags=tags_per_source.get(s.id, []),
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
        n_reviewed, total_sources = db.screen_counts(pid, rid)
        counts_text = f"{n_reviewed} / {total_sources} reviewed by you • {total} match current filter"
        return cards, prev_disabled, next_disabled, page_info, counts_text


def _meta_line(src: Source) -> str:
    parts: list[str] = []
    if src.journal:
        parts.append(src.journal)
    if src.year:
        parts.append(str(src.year))
    if src.source_database:
        parts.append(f"[{src.source_database}]")
    return " • ".join(parts)


def _source_card(
    src: Source,
    my_decision: Optional[str],
    workflow: str,
    peer_count: int,
    abstract_open: bool = False,
    reviewer_id: str = "",
    tags: Optional[list[dict]] = None,
    note_count: int = 0,
) -> Any:
    sid = src.id
    decision_color = {
        "include": "success",
        "exclude": "danger",
        "uncertain": "warning",
    }

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
                id={"type": "screen-reset", "source": sid},
                size="sm",
                color="link",
                className="p-0 text-decoration-none",
            ),
        ]
    else:
        right = [
            dbc.ButtonGroup(
                [
                    dbc.Button(
                        "Include",
                        id={"type": "screen-decide", "source": sid, "decision": "include"},
                        color="success",
                        size="sm",
                    ),
                    dbc.Button(
                        "Exclude",
                        id={"type": "screen-decide", "source": sid, "decision": "exclude"},
                        color="danger",
                        size="sm",
                    ),
                    dbc.Button(
                        "Uncertain",
                        id={"type": "screen-decide", "source": sid, "decision": "uncertain"},
                        color="warning",
                        size="sm",
                    ),
                ],
            ),
        ]

    peer_indicator: Any = None
    if workflow == "independent" and peer_count > 0:
        peer_indicator = html.Small(
            f"{peer_count} other reviewer(s) voted",
            className="text-muted d-block mt-1",
        )

    doi_el: Any = None
    if src.doi:
        doi_el = html.Div(
            html.A(
                f"DOI: {src.doi}",
                href=f"https://doi.org/{src.doi}",
                target="_blank",
                className="small",
            )
        )

    abstract_btn = html.Div(
        dbc.Button(
            "Abstract ▼",
            id={"type": "screen-abstract-btn", "source": sid},
            size="sm",
            color="link",
            className="p-0",
        ),
        className="mt-1",
    )

    abstract_body = dbc.Collapse(
        html.P(src.abstract or "(no abstract)", className="mt-2 small", style={"whiteSpace": "pre-wrap"}),
        id={"type": "screen-abstract-body", "source": sid},
        is_open=abstract_open,
    )

    history_btn = html.Div(
        [
            dbc.Button(
                "History",
                id={"type": "screen-history-btn", "source": sid},
                size="sm",
                color="link",
                className="p-0 me-3",
            ),
            dbc.Button(
                "Tags",
                id={"type": "screen-tag-btn", "source": sid},
                size="sm",
                color="link",
                className="p-0 me-3",
            ),
            dbc.Button(
                f"Note ({note_count})" if note_count else "Note",
                id={"type": "screen-note-btn", "source": sid},
                size="sm",
                color="link",
                className="p-0 me-3",
            ),
            dbc.Button(
                "Duplicate",
                id={"type": "screen-duplicate", "source": sid},
                size="sm",
                color="link",
                className="p-0 text-danger",
            ),
        ],
        className="mt-1",
    )

    left_top = html.Div(
        [
            html.Strong(f"#{sid}  ", className="text-muted"),
            html.Span(_short_author_year(src), className="text-muted me-2"),
        ]
    )
    title_el = html.H6(src.title, className="mb-1")
    meta_el = html.P(_meta_line(src), className="text-muted small mb-1")
    tag_chips_el: Any = html.Span()
    if tags:
        tag_chips_el = html.Div(
            [
                dbc.Badge(
                    t["name"],
                    color=t.get("color") or "secondary",
                    pill=True,
                    className="me-1",
                )
                for t in tags
            ],
            className="mt-1 mb-1",
        )

    return dbc.Card(
        dbc.CardBody(
            [
                dbc.Row(
                    [
                        dbc.Col(
                            [left_top, title_el, meta_el, tag_chips_el, doi_el, abstract_btn, abstract_body, history_btn],
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


def _history_block(actions: list[dict], src: Source, show_reviewer: bool) -> Any:
    items: list[Any] = [
        html.Div(
            [
                html.Span("📥  ", className="me-1"),
                html.Strong("Imported", className="me-2"),
                html.Small(str(src.imported_at) if src.imported_at else "", className="text-muted"),
            ],
            className="mb-1",
        )
    ]
    if not actions:
        items.append(html.Small("(no actions yet)", className="text-muted"))
        return html.Div(items, className="mt-2 small")

    for a in actions:
        ts = a.get("timestamp", "")
        reviewer = a.get("reviewer_id", "?")
        by_part = html.Small(f"by {reviewer}", className="text-muted ms-2") if show_reviewer else None
        if a["action"] == "vote":
            color = {"include": "success", "exclude": "danger", "uncertain": "warning"}.get(a.get("decision", ""), "secondary")
            row_children = [
                dbc.Badge(a.get("decision", "").upper(), color=color, className="me-2"),
                html.Span("vote"),
            ]
            if by_part:
                row_children.append(by_part)
            row_children.append(html.Small(f"  {ts}", className="text-muted ms-2"))
            items.append(html.Div(row_children, className="mb-1"))
        elif a["action"] == "reset":
            row_children = [
                html.Span("↻  ", className="me-1"),
                html.Strong("Reset"),
            ]
            if by_part:
                row_children.append(by_part)
            row_children.append(html.Small(f"  {ts}", className="text-muted ms-2"))
            items.append(html.Div(row_children, className="mb-1"))
        elif a["action"] == "reconcile":
            color = {"include": "success", "exclude": "danger"}.get(a.get("decision", ""), "secondary")
            row_children = [
                html.Span("⚖  ", className="me-1"),
                html.Strong("Final: "),
                dbc.Badge(a.get("decision", "").upper(), color=color, className="me-2"),
            ]
            if by_part:
                row_children.append(by_part)
            row_children.append(html.Small(f"  {ts}", className="text-muted ms-2"))
            items.append(html.Div(row_children, className="mb-1"))
        elif a["action"] == "reconcile_undo":
            row_children = [
                html.Span("↻  ", className="me-1"),
                html.Strong("Undo reconciliation"),
            ]
            if by_part:
                row_children.append(by_part)
            row_children.append(html.Small(f"  {ts}", className="text-muted ms-2"))
            items.append(html.Div(row_children, className="mb-1"))
        elif a["action"] == "move_to_screening":
            row_children = [
                html.Span("↺  ", className="me-1"),
                html.Strong("Moved back to screening"),
            ]
            if by_part:
                row_children.append(by_part)
            row_children.append(html.Small(f"  {ts}", className="text-muted ms-2"))
            items.append(html.Div(row_children, className="mb-1"))
        elif a["action"] == "move_to_full_text":
            row_children = [
                html.Span("↺  ", className="me-1"),
                html.Strong("Moved back to full-text"),
            ]
            if by_part:
                row_children.append(by_part)
            row_children.append(html.Small(f"  {ts}", className="text-muted ms-2"))
            items.append(html.Div(row_children, className="mb-1"))
    return html.Div(items, className="mt-2 small")


