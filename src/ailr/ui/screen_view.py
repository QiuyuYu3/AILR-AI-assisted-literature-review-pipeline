"""Screening review: card list with inline decision buttons."""

import json
import time
from pathlib import Path
from typing import Any, Optional

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update

from ailr.core.config import team_size_for
from ailr.core.source import Source
from ailr.extraction import compose_screening_prompt
from ailr.ui import ai_runner, version_ui
from ailr.ui._actions import _apply_reset, _apply_vote
from ailr.ui._cards import (
    action_banner,
    decision_controls,
    doi_link,
    header_line,
    meta_line,
    peer_note,
    tag_chips,
)
from ailr.ui._common import help_icon, prompt_view_toggle, render_prompt_body, triggered_click_id, with_help

from ailr.ui._project import (
    get_project,
    read_criteria,
    read_screening_additional,
    read_screening_prompt,
    read_text_or,
)


def _screen_prompt_text() -> str:
    return read_screening_prompt()


def _screen_additional_text() -> str:
    return read_screening_additional()


def _screening_composed_pre(text: str, additional: str, mode: str = "plain") -> Any:
    """The 'Full prompt preview' content. Used for the initial render and the live-update callback."""
    composed = compose_screening_prompt(text or "", criteria=read_criteria(), additional=additional or "")
    return render_prompt_body(composed + "\n\n--- [THE ABSTRACT IS APPENDED HERE AUTOMATICALLY] ---", mode)



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
    {"label": "Cross-check flagged", "value": "crosscheck_flagged"},
    {"label": "Last quick test", "value": "quick_test"},
    {"label": "All", "value": "all"},
]

_REVIEW_VALUES = {"to_screen", "reviewed"}
# Results of an automated run, not a position in the review queue.
_CHECK_VALUES = {"crosscheck_flagged", "quick_test"}


def _status_groups() -> tuple[list[dict], list[dict]]:
    """Status options split into review / check-result groups ('All' excluded)."""
    review = [o for o in _STATUS_FILTERS if o["value"] in _REVIEW_VALUES]
    checks = [o for o in _STATUS_FILTERS if o["value"] in _CHECK_VALUES]
    return review, checks


def _status_group_of(value: str) -> str:
    if value in _CHECK_VALUES:
        return "checks"
    if value == "all":
        return "all"
    return "review"

_SORT_OPTIONS = [
    {"label": "ID", "value": "id"},
    {"label": "Author", "value": "author"},
    {"label": "Title", "value": "title"},
    {"label": "Year (newest)", "value": "year_desc"},
    {"label": "Year (oldest)", "value": "year_asc"},
    {"label": "AI confidence (lowest first)", "value": "confidence_asc"},
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
        dbc.Textarea(id="screen-additional", value=_screen_additional_text(), style={"height": "120px", "fontFamily": "monospace", "fontSize": "0.88rem"}),
        dbc.Button("Save additional instructions", id="screen-additional-save", color="primary", size="sm", className="mt-1"),
        html.Div(id="screen-additional-feedback", className="small mt-1"),
        with_help(
            html.H6("Full prompt preview", className="mb-0 me-1"),
            "The exact prompt sent to the AI, with your criteria and additional instructions filled in.",
            "screen-preview-help",
        ),
        prompt_view_toggle("screen-prompt-render"),
        html.Div(id="screen-prompt-composed", children=_screening_composed_pre(_screen_prompt_text(), _screen_additional_text())),
        html.Details(
            [
                html.Summary("Version history & diff"),
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
                        dbc.Textarea(id="screen-prompt", value=_screen_prompt_text(), style={"height": "220px", "fontFamily": "monospace", "fontSize": "0.88rem"}),
                        dbc.Button("Save prompt", id="screen-prompt-save", color="primary", size="sm", className="mt-1"),
                        html.Div(id="screen-prompt-feedback", className="small mt-1"),
                    ],
                    className="ps-2",
                ),
            ],
            className="mt-3",
        ),
    ]


def _screening_cc_prompt_text() -> str:
    from ailr.crosschecker import BUILT_IN_SCREENING_PROMPT, load_prompt

    project = get_project()
    return load_prompt(project.root, project.config.crosscheck.screening_prompt, BUILT_IN_SCREENING_PROMPT)


def _screening_cc_additional_text() -> str:
    project = get_project()
    return read_text_or(project.root / project.config.crosscheck.screening_additional, "")


def _screening_cc_composed_pre(prompt: str, additional: str, mode: str = "plain") -> Any:
    from ailr.crosschecker import compose_screening_crosscheck_prompt

    composed = compose_screening_crosscheck_prompt(
        prompt or "",
        project_name=get_project().config.project.name,
        criteria=read_criteria(),
        additional=additional or "",
    )
    tail = "\n\n--- [THE RECORDED DECISION AND THE TITLE/ABSTRACT ARE APPENDED HERE AUTOMATICALLY] ---"
    return render_prompt_body(composed + tail, mode)


def screening_crosscheck_prompt_panel() -> list[Any]:
    """The checker's prompt, edited beside the screening prompt because it judges that stage's output."""
    return [
        dbc.Alert(
            [
                html.Strong("This is the prompt for cross-check, not for screening. "),
                "It is given a decision that already exists — the verdict, its reason and the quotes "
                "offered as support — plus the title and abstract, and asked whether the abstract backs "
                "it up. ailr ships a default, so you only need to touch this if your criteria need "
                "domain-specific guidance.",
            ],
            color="light", className="small py-2 mt-2",
        ),
        with_help(
            html.H6("Additional instructions (optional)", className="mb-0 me-1"),
            "Appended to the cross-check prompt — e.g. how strict to be about a criterion the abstract rarely states outright.",
            "screen-cc-additional-help",
        ),
        dbc.Textarea(
            id="screen-cc-additional",
            value=_screening_cc_additional_text(),
            placeholder="e.g. Treat a study described only as 'pilot' as uncertain rather than exclude.",
            style={"height": "120px", "fontFamily": "monospace", "fontSize": "0.88rem"},
        ),
        html.Div(
            dbc.Button("Save additional instructions", id="screen-cc-additional-save", color="primary", size="sm"),
            className="mt-2",
        ),
        html.Div(id="screen-cc-additional-feedback", className="small mt-1"),
        with_help(
            html.H6("Full prompt preview", className="mb-0 me-1"),
            "The exact prompt sent to the checker, with your criteria and additional instructions filled in.",
            "screen-cc-preview-help",
        ),
        prompt_view_toggle("screen-cc-prompt-render"),
        html.Div(id="screen-cc-prompt-composed",
                 children=_screening_cc_composed_pre(_screening_cc_prompt_text(), _screening_cc_additional_text())),
        html.Details(
            [
                html.Summary("Advanced: edit the full cross-check template"),
                dbc.Alert(
                    [
                        html.Strong("Most users don't need this. "),
                        "Keep the markers ", html.Code("{{project_name}}"), ", ", html.Code("{{criteria}}"),
                        " and ", html.Code("{{additional}}"), " so ailr can fill them in. Saving writes ",
                        html.Code("prompts/crosscheck_screening.txt"), "; delete that file to go back to the built-in prompt.",
                    ],
                    color="light", className="small py-2 mt-2",
                ),
                dbc.Textarea(id="screen-cc-prompt", value=_screening_cc_prompt_text(),
                             style={"height": "260px", "fontFamily": "monospace", "fontSize": "0.88rem"}),
                html.Div(
                    [
                        dbc.Button("Save prompt", id="screen-cc-prompt-save", color="primary", size="sm", className="me-2"),
                        dbc.Button("Restore built-in prompt", id="screen-cc-prompt-builtin", color="secondary", outline=True, size="sm"),
                    ],
                    className="mt-2",
                ),
                html.Div(id="screen-cc-prompt-feedback", className="small mt-1"),
            ],
            className="mt-3",
        ),
    ]


def ai_screening_panel() -> list[Any]:
    """Run AI screening + import externally-run results. Rendered on the abstract AI screening tab."""
    return [
        dbc.Label("Run AI screening", className="fw-bold"),
        html.P("Runs AI on the abstracts and records its decisions (the prompt is snapshotted as a version).", className="text-muted small mb-1"),
        dbc.Switch(id="screen-ai-mock", label="Mock (no API calls)", value=True, className="small"),
        dbc.Switch(id="screen-ai-force", label="Re-screen everything (including already-decided papers)", value=False, className="small"),
        html.P("Turn this on after editing the criteria or the prompt: the normal run skips any paper the AI has already decided, so those keep their old verdict. Re-screening appends a new decision and leaves the old one as history.",
               className="text-muted small mb-1"),
        html.P("For every decision the AI also records a PASS / FAIL / UNCERTAIN verdict, reason, and confidence for each criterion (shown in the review). This keeps each include/exclude auditable.",
               className="text-muted small mb-1"),
        dbc.Button("Run AI screening", id="screen-ai-run", color="primary", outline=True, size="sm"),
        dcc.ConfirmDialog(
            id="screen-ai-force-confirm",
            message="Re-screen EVERY paper, including ones the AI has already decided?\n\n"
                    "This makes a fresh API call for each paper, so a full corpus costs real money. "
                    "Each new decision is appended; the earlier ones are kept as history.",
        ),
        html.Div(id="screen-ai-status", className="small mt-2"),
        dcc.Interval(id="screen-ai-poll", interval=1200, disabled=True),
        dcc.ConfirmDialogProvider(
            dbc.Button("Clear mock AI results", color="link", size="sm", className="text-danger p-0 mt-2"),
            id="screen-clear-mock",
            message="Delete all MOCK AI screening decisions in this project? Real AI and human decisions are kept.",
        ),
        html.Div(id="screen-clear-mock-status", className="small mt-1"),
        html.Hr(className="my-3"),
        dbc.Label("Cross-check", className="fw-bold"),
        html.P("Checks decisions already recorded against the title and abstract: evidence quotes matched "
               "verbatim, criterion IDs that exist, and whether the decision squares with its own "
               "per-criterion verdicts. No API calls. Findings are advisory and never change a decision "
               "or enter agreement statistics.",
               className="text-muted small mb-1"),
        dbc.Button("Cross-check all decisions", id="screen-crosscheck-all", color="secondary", outline=True, size="sm"),
        html.Div(id="screen-crosscheck-all-status", className="small mt-2"),
        dcc.Interval(id="screen-crosscheck-all-poll", interval=1200, disabled=True),
        html.P("A second model can also re-judge each decision against the abstract. This one costs tokens, "
               "needs a different model from the one that screened (Settings -> Cross-check), and is one call "
               "per paper — at this stage that is the whole corpus, so it is the expensive button on the page.",
               className="text-muted small mb-1 mt-3"),
        dbc.Switch(id="screen-crosscheck-llm-mock", label="Mock (no API calls)", value=True, className="small"),
        dbc.Button("LLM cross-check all decisions", id="screen-crosscheck-llm", color="secondary", outline=True, size="sm"),
        html.Div(id="screen-crosscheck-llm-status", className="small mt-2"),
        dcc.Interval(id="screen-crosscheck-llm-poll", interval=1500, disabled=True),
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
    review_opts, check_opts = _status_groups()
    return dbc.Row(
        [
            dbc.Col(
                [
                    # One logical filter, three radio groups: the merged value lives in the store and
                    # `_sync_status` keeps only one group selected at a time.
                    dcc.Store(id="screen-filter-status", data="to_screen"),
                    dbc.Label("Status", className="fw-bold"),
                    html.Div("Review", className="small text-muted text-uppercase mt-1"),
                    dbc.RadioItems(
                        id="screen-status-review",
                        options=review_opts,
                        value="to_screen",
                        persistence=True,
                        persistence_type="session",
                    ),
                    html.Div("Checks", className="small text-muted text-uppercase mt-2"),
                    dbc.RadioItems(
                        id="screen-status-checks",
                        options=check_opts,
                        value=None,
                        persistence=True,
                        persistence_type="session",
                    ),
                    dbc.RadioItems(
                        id="screen-status-all",
                        options=[{"label": "All", "value": "all"}],
                        value=None,
                        className="mt-2",
                        persistence=True,
                        persistence_type="session",
                    ),

                    html.Hr(),

                    dbc.Label("Sort", className="fw-bold"),
                    dbc.Select(id="screen-sort", options=_SORT_OPTIONS, value="id", persistence=True, persistence_type="session"),

                    dbc.Label("Display", className="fw-bold mt-2"),
                    dbc.Select(id="screen-pagesize", options=_PAGE_SIZES, value="25", persistence=True, persistence_type="session"),

                    dbc.Switch(
                        id="screen-expand-all",
                        label="Expand all abstracts",
                        value=True,
                        className="mt-2",
                        label_class_name="fw-bold",
                        persistence=True,
                        persistence_type="session",
                    ),

                    html.Hr(),

                    dbc.Label("Filter", className="fw-bold"),

                    dbc.Label("Tags", className="small mt-1"),
                    dbc.Select(
                        id="screen-tags-filter",
                        options=[{"label": "(any)", "value": ""}],
                        value="",
                        className="mb-2",
                        persistence=True,
                        persistence_type="session",
                    ),

                    dbc.Label("Keyword search", className="small"),
                    dbc.Input(
                        id="screen-search",
                        placeholder="Text or #123, press Enter",
                        debounce=True,
                        className="mb-2",
                        persistence=True,
                        persistence_type="session",
                    ),

                    dbc.Label("Within", className="small"),
                    dbc.RadioItems(
                        id="screen-within",
                        options=_WITHIN_OPTIONS,
                        value="title_and_abstract",
                        className="mb-2",
                        persistence=True,
                        persistence_type="session",
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
    def _screening_started_alert(mock: Any, force: bool) -> Any:
        started = ai_runner.start_screening(get_project(), bool(mock), force=force)
        msg = "AI screening started…" if started else "Already running…"
        return dbc.Alert(msg, color="info", className="py-1 mb-0")

    @app.callback(
        Output("screen-ai-force-confirm", "displayed"),
        Output("screen-ai-poll", "disabled"),
        Output("screen-ai-status", "children"),
        Input("screen-ai-run", "n_clicks"),
        State("screen-ai-mock", "value"),
        State("screen-ai-force", "value"),
        prevent_initial_call=True,
    )
    def _ai_run(n, mock, force):
        if not n:
            return no_update, no_update, no_update
        if force:  # hand off to the confirm dialog; the run starts in _ai_run_forced
            return True, no_update, no_update
        return False, False, _screening_started_alert(mock, False)

    @app.callback(
        Output("screen-ai-poll", "disabled", allow_duplicate=True),
        Output("screen-ai-status", "children", allow_duplicate=True),
        Input("screen-ai-force-confirm", "submit_n_clicks"),
        State("screen-ai-mock", "value"),
        prevent_initial_call=True,
    )
    def _ai_run_forced(n, mock):
        if not n:
            return no_update, no_update
        return False, _screening_started_alert(mock, True)

    @app.callback(
        Output("screen-clear-mock-status", "children"),
        Output("screen-refresh", "data", allow_duplicate=True),
        Input("screen-clear-mock", "submit_n_clicks"),
        prevent_initial_call=True,
    )
    def _clear_mock(n):
        if not n:
            return no_update, no_update
        project = get_project()
        cleared = project.db.clear_mock_ai_decisions(project.project_id)
        return dbc.Alert(f"Cleared {cleared} mock AI screening decision(s).", color="success", className="py-1 mb-0"), {"ts": time.time()}

    @app.callback(
        Output("screen-ai-status", "children", allow_duplicate=True),
        Output("screen-ai-poll", "disabled", allow_duplicate=True),
        Output("screen-refresh", "data", allow_duplicate=True),
        Output("screen-prompt-ver-select", "options", allow_duplicate=True),
        Output("screen-prompt-ver-b", "options", allow_duplicate=True),
        Input("screen-ai-poll", "n_intervals"),
        prevent_initial_call=True,
    )
    def _ai_poll(_n):
        st = ai_runner.get_status("screening")
        if st.get("running"):
            done, total = st.get("done", 0), st.get("total", 0)
            pct = int(done / total * 100) if total else 0
            bar = dbc.Progress(value=pct, label=f"{done}/{total}", striped=True, animated=True, className="mt-1")
            return html.Div(["Running AI screening…", bar]), False, no_update, no_update, no_update
        if st.get("error"):
            return dbc.Alert(f"AI screening failed: {st['error']}", color="danger", className="py-1 mb-0"), True, no_update, no_update, no_update
        if st.get("started") and st.get("summary"):
            # A run may have just snapshotted a new prompt version — refresh both dropdowns.
            opts = _prompt_version_options()
            return dbc.Alert(st["summary"], color="success", className="py-1 mb-0"), True, {"ts": time.time()}, opts, opts
        return no_update, True, no_update, no_update, no_update

    def _write_project_file(rel: str, text: str) -> Any:
        p = get_project().root / rel
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text or "", encoding="utf-8")
        except OSError as e:
            return dbc.Alert(f"Save failed: {e}", color="danger", className="mb-0 py-1")
        return dbc.Alert(f"Saved to {p.name}.", color="success", className="mb-0 py-1")

    @app.callback(
        Output("screen-cc-additional-feedback", "children"),
        Input("screen-cc-additional-save", "n_clicks"),
        State("screen-cc-additional", "value"),
        prevent_initial_call=True,
    )
    def _save_screen_cc_additional(n, text):
        if not n:
            return no_update
        return _write_project_file(get_project().config.crosscheck.screening_additional, text)

    @app.callback(
        Output("screen-cc-prompt-feedback", "children"),
        Input("screen-cc-prompt-save", "n_clicks"),
        State("screen-cc-prompt", "value"),
        prevent_initial_call=True,
    )
    def _save_screen_cc_prompt(n, text):
        if not n:
            return no_update
        if not str(text or "").strip():
            return dbc.Alert("Prompt is empty. Delete prompts/crosscheck_screening.txt to use the built-in one.",
                             color="warning", className="mb-0 py-1")
        return _write_project_file(get_project().config.crosscheck.screening_prompt, text)

    @app.callback(
        Output("screen-cc-prompt", "value"),
        Output("screen-cc-prompt-feedback", "children", allow_duplicate=True),
        Input("screen-cc-prompt-builtin", "n_clicks"),
        prevent_initial_call=True,
    )
    def _restore_builtin_screen_cc_prompt(n):
        if not n:
            return no_update, no_update
        from importlib.resources import files

        from ailr.crosschecker import BUILT_IN_SCREENING_PROMPT

        text = (files("ailr") / BUILT_IN_SCREENING_PROMPT).read_text(encoding="utf-8")
        return text, dbc.Alert("Built-in prompt loaded into the editor. Save to write it to the project.",
                               color="info", className="mb-0 py-1")

    @app.callback(
        Output("screen-cc-prompt-composed", "children"),
        Input("screen-cc-prompt", "value"),
        Input("screen-cc-additional", "value"),
        Input("screen-cc-prompt-render", "value"),
    )
    def _screen_cc_preview(prompt, additional, mode):
        return _screening_cc_composed_pre(prompt, additional, mode or "plain")

    def _crosscheck_poll_result(job_key: str):
        st = ai_runner.get_status(job_key)
        if st.get("running"):
            done, total = st.get("done", 0), st.get("total", 0)
            label = f"Cross-checking… {done}/{total}" if total else "Cross-checking…"
            return html.Small(label, className="text-muted"), False, no_update
        if st.get("error"):
            return dbc.Alert(f"Cross-check failed: {st['error']}", color="danger", className="py-1 mb-0"), True, no_update
        if st.get("summary"):
            return dbc.Alert(st["summary"], color="info", className="py-1 mb-0"), True, {"ts": time.time()}
        return no_update, True, no_update

    @app.callback(
        Output("screen-crosscheck-all-poll", "disabled"),
        Output("screen-crosscheck-all-status", "children"),
        Input("screen-crosscheck-all", "n_clicks"),
        prevent_initial_call=True,
    )
    def _crosscheck_all(n):
        if not n:
            return no_update, no_update
        if not ai_runner.start_screening_crosscheck(get_project(), stage="abstract"):
            return True, dbc.Alert("A cross-check run is already in progress.", color="warning", className="py-1 mb-0")
        return False, dbc.Alert("Cross-checking every screening decision…", color="info", className="py-1 mb-0")

    @app.callback(
        Output("screen-crosscheck-all-status", "children", allow_duplicate=True),
        Output("screen-crosscheck-all-poll", "disabled", allow_duplicate=True),
        Output("screen-refresh", "data", allow_duplicate=True),
        Input("screen-crosscheck-all-poll", "n_intervals"),
        prevent_initial_call=True,
    )
    def _crosscheck_all_poll(_n):
        return _crosscheck_poll_result("crosscheck-screening-abstract")

    @app.callback(
        Output("screen-crosscheck-llm-poll", "disabled"),
        Output("screen-crosscheck-llm-status", "children"),
        Input("screen-crosscheck-llm", "n_clicks"),
        State("screen-crosscheck-llm-mock", "value"),
        prevent_initial_call=True,
    )
    def _crosscheck_llm(n, mock):
        if not n:
            return no_update, no_update
        if not ai_runner.start_screening_llm_crosscheck(get_project(), "abstract", bool(mock)):
            return True, dbc.Alert("An LLM cross-check run is already in progress.", color="warning", className="py-1 mb-0")
        note = " (mock)" if mock else ""
        return False, dbc.Alert(f"LLM cross-check started{note}…", color="info", className="py-1 mb-0")

    @app.callback(
        Output("screen-crosscheck-llm-status", "children", allow_duplicate=True),
        Output("screen-crosscheck-llm-poll", "disabled", allow_duplicate=True),
        Output("screen-refresh", "data", allow_duplicate=True),
        Input("screen-crosscheck-llm-poll", "n_intervals"),
        prevent_initial_call=True,
    )
    def _crosscheck_llm_poll(_n):
        return _crosscheck_poll_result("crosscheck-screening-llm-abstract")

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
                        html.Pre(composed, style={"whiteSpace": "pre-wrap", "fontSize": "0.85rem", "maxHeight": "300px", "overflow": "auto"}),
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
        Input("screen-prompt-render", "value"),
    )
    def _composed_screen_prompt(text, additional, mode):
        return _screening_composed_pre(text, additional, mode or "plain")

    @app.callback(
        Output("screen-filter-status", "data"),
        Output("screen-status-review", "value"),
        Output("screen-status-checks", "value"),
        Output("screen-status-all", "value"),
        Input("screen-status-review", "value"),
        Input("screen-status-checks", "value"),
        Input("screen-status-all", "value"),
    )
    def _sync_status(review, checks, all_v):
        """Keep the three status groups mutually exclusive and publish the single active value."""
        trigger = ctx.triggered_id
        if trigger == "screen-status-review" and review:
            value = review
        elif trigger == "screen-status-checks" and checks:
            value = checks
        elif trigger == "screen-status-all" and all_v:
            value = all_v
        else:
            # Initial load (persistence restore) or a group cleared by its own click.
            value = review or checks or all_v or "to_screen"
        group = _status_group_of(value)
        return (
            value,
            value if group == "review" else None,
            value if group == "checks" else None,
            value if group == "all" else None,
        )

    @app.callback(
        Output("screen-search", "value"),
        Output("screen-within", "value"),
        Output("screen-status-review", "value", allow_duplicate=True),
        Output("screen-status-checks", "value", allow_duplicate=True),
        Output("screen-status-all", "value", allow_duplicate=True),
        Input("screen-reset-filters", "n_clicks"),
        prevent_initial_call=True,
    )
    def _reset_filters(_clicks):
        return "", "title_and_abstract", "to_screen", None, None

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
        Input("screen-filter-status", "data"),
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
            workflow = get_project().config.screening_workflow("abstract")
            return _apply_vote(db, int(triggered["source"]), triggered["decision"], rid, workflow)

        if isinstance(triggered, dict) and triggered.get("type") == "screen-reset":
            return _apply_reset(db, int(triggered["source"]), rid)

        return {"ts": time.time()}, no_update

    @app.callback(
        Output("screen-action-banner", "children"),
        Input("screen-last-action", "data"),
    )
    def _render_banner(last):
        return action_banner(last, undo_id="screen-banner-undo", verb="screened")

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
        return _apply_reset(get_project().db, int(sid), rid)

    @app.callback(
        Output({"type": "screen-abstract-body", "source": ALL}, "is_open"),
        Input({"type": "screen-abstract-btn", "source": ALL}, "n_clicks"),
        State({"type": "screen-abstract-body", "source": ALL}, "is_open"),
        prevent_initial_call=True,
    )
    def _toggle_abstract(clicks, is_open_list):
        triggered = triggered_click_id()
        if triggered is None:
            return no_update
        target_sid = triggered.get("source")
        ids = [t["id"] for t in ctx.inputs_list[0]]
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
        Input("screen-filter-status", "data"),
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
        workflow = project.config.screening_workflow("abstract")

        if not rid:
            empty = dbc.Alert("Enter your reviewer ID above to begin.", color="info")
            return empty, True, True, "", ""

        # Team-aware "To screen": independent = 2 humans per paper, assisted = 1 human (+ AI).
        team_size = team_size_for(workflow)
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
        from ailr.ui.ai_runner import current_screening_composed
        stale_ids = db.stale_ai_screening_source_ids(pid, current_screening_composed(project), stage="abstract")
        cc_counts = db.cross_check_counts(visible_ids, stage="abstract", target_type="ai")

        cards = [
            _source_card(
                s, my_decisions.get(s.id), workflow, peer_counts.get(s.id, 0),
                bool(expand_all), rid,
                tags=tags_per_source.get(s.id, []),
                note_count=note_counts.get(s.id, 0),
                stale=s.id in stale_ids,
                crosscheck_flagged=cc_counts.get(s.id, 0),
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
        if stale_ids:
            counts_text += f" • {len(stale_ids)} AI screening(s) outdated — re-run with ‘Re-screen everything’"
        return cards, prev_disabled, next_disabled, page_info, counts_text


def _source_card(
    src: Source,
    my_decision: Optional[str],
    workflow: str,
    peer_count: int,
    abstract_open: bool = False,
    reviewer_id: str = "",
    tags: Optional[list[dict]] = None,
    note_count: int = 0,
    stale: bool = False,
    crosscheck_flagged: int = 0,
) -> Any:
    sid = src.id
    # Findings describe the AI's record, so they stay hidden until this reviewer has voted — the
    # same blinding rule that keeps the AI decision itself off this card.
    show_crosscheck = bool(crosscheck_flagged and my_decision)
    right = decision_controls(sid, my_decision, prefix="screen")
    peer_indicator = peer_note(workflow, peer_count)
    doi_el = doi_link(src)

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
                f"Cross-check ({crosscheck_flagged})",
                id={"type": "screen-crosscheck-btn", "source": sid},
                size="sm",
                color="link",
                className="p-0 me-3 text-warning",
            ) if show_crosscheck else None,
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

    stale_badge = dbc.Badge(
        "AI screening outdated", color="warning", className="ms-1",
        title="Criteria or the screening prompt changed since this paper was AI-screened.",
    ) if stale else None
    cc_badge = dbc.Badge(
        "Cross-check flagged", color="warning", className="ms-1",
        title="A deterministic check flagged the AI's record for this paper. Advisory only.",
    ) if show_crosscheck else None
    left_top = header_line(src, badges=[stale_badge, cc_badge])
    title_el = html.H6(src.title, className="mb-1")
    meta_el = meta_line(src, include_database=True)
    tag_chips_el = tag_chips(tags)

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


_CC_FIELD_LABEL = {
    "decision": "Decision",
    "reasoning": "Reason",
    "evidence_quotes": "Evidence quotes",
    "matched_criteria": "Cited criteria",
}


def crosscheck_findings_block(findings: list[dict]) -> Any:
    """Open findings for one screening decision. Advisory: nothing here changes a decision, and a
    quote the checker could not match is often a formatting difference, not a fabrication."""
    open_findings = [f for f in findings if (f.get("verdict") or "") != "agree" and not f.get("stale")]
    if not open_findings:
        return html.P("Cross-checked, nothing flagged.", className="text-muted small mb-0")

    items = []
    for f in open_findings:
        name = f.get("field_name") or ""
        label = _CC_FIELD_LABEL.get(name, name)
        code = f.get("issue_code") or f.get("check_kind") or ""
        items.append(
            html.Li(
                [
                    html.Strong(f"{label}: ", className="me-1"),
                    html.Span(f.get("reason") or "", className="me-1"),
                    dbc.Badge(code, color="light", text_color="secondary", className="ms-1") if code else None,
                ],
                className="small mb-1",
            )
        )
    return html.Div(
        [
            html.P(f"{len(open_findings)} finding(s). These are advisory and never change a decision.",
                   className="text-muted small"),
            html.Ul(items, className="mb-0"),
        ]
    )


def _rationale_line(a: dict) -> Any:
    """The reason typed on an adjudication, or the PRISMA reasons picked on a full-text exclude."""
    text = (a.get("rationale") or "").strip()
    if not text:
        return None
    return html.Div(html.Small(text, className="text-muted fst-italic"), className="ms-4 mb-1")


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
            items.append(_rationale_line(a))
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
                # Named even in the per-reviewer timeline, which shows adjudications by anyone:
                # unattributed, someone else's ruling would read as your own.
                html.Small(f"by {reviewer}", className="text-muted ms-2"),
                html.Small(f"  {ts}", className="text-muted ms-2"),
            ]
            items.append(html.Div(row_children, className="mb-1"))
            items.append(_rationale_line(a))
        elif a["action"] == "reconcile_undo":
            row_children = [
                html.Span("↻  ", className="me-1"),
                html.Strong("Undo reconciliation"),
                html.Small(f"by {reviewer}", className="text-muted ms-2"),
                html.Small(f"  {ts}", className="text-muted ms-2"),
            ]
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
    return html.Div([i for i in items if i is not None], className="mt-2 small")


