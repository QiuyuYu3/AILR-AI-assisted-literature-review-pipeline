"""Calibration / prompt-test area, embedded as a tab on each stage's Workflow page.

stage="abstract"   — test the SCREENING prompt:
    Quick test (isolated test tables) + Full calibration (κ vs human).
stage="extraction" — test the EXTRACTION prompt:
    Quick test only: run AI extraction on a few papers, view fields + derived full-text decision.
"""

from typing import Any

import dash_bootstrap_components as dbc
from dash import Input, Output, State, dcc, html, no_update

from ailr.ui import ai_runner
from ailr.ui._common import get_project

_DECISION_COLOR = {"include": "success", "exclude": "danger", "uncertain": "warning"}

_MODE_OPTIONS = [
    {"label": "Quick test — run AI, eyeball output (not saved to the review)", "value": "quick"},
    {"label": "Full calibration — AI + human blind review → κ", "value": "full"},
]


def _prefix(stage: str) -> str:
    return "cal-abs" if stage == "abstract" else "cal-ext"


def _test_stage(stage: str) -> str:
    return "abstract" if stage == "abstract" else "extraction"


def _run_options(stage_table: str) -> list[dict]:
    project = get_project()
    runs = project.db.list_test_runs(project.project_id, stage_table)
    return [
        {"label": f"#{r['id']} • {r['created_at']} • n={r['sample_size']}",
         "value": str(r["id"])}
        for r in runs
    ]


def _author_year_src(s: Any) -> str:
    ay = s.authors[0].split(",")[0].strip() if getattr(s, "authors", None) else ""
    return f"{ay} {s.year}".strip() if getattr(s, "year", None) else ay


def _pick_options(test_stage: str) -> list[dict]:
    """Candidate papers for the 'pick specific papers' dropdown, searchable by the label text
    (author / year / title / id / doi)."""
    project = get_project()
    if test_stage == "extraction":
        sources = [s for s in project.db.list_sources_with_markdown(project.project_id) if s.markdown_path]
    else:
        sources = [s for s in project.db.list_sources(project.project_id) if s.abstract]
    options = []
    for s in sources:
        doi = getattr(s, "doi", None)
        label = f"{_author_year_src(s)} — {s.title or '(untitled)'} (#{s.id})" + (f" · {doi}" if doi else "")
        options.append({"label": label, "value": s.id})
    return options


def _picked_ids(picked: Any) -> list[int]:
    out: list[int] = []
    for x in (picked or []):
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            pass
    return out


def _running_alert(started: bool) -> Any:
    return dbc.Alert("Running…" if started else "Already running…", color="info", className="py-1 mb-0")


def _bad_n_alert() -> Any:
    return dbc.Alert("Enter a valid sample size.", color="warning", className="py-1 mb-0")


def layout(stage: str = "abstract") -> Any:
    p = _prefix(stage)
    project = get_project()
    if stage == "abstract":
        default_n = min(5, project.config.screening.calibration.min)
        intro = "Try the screening prompt on a sample before running the whole corpus."
        mode_block = [
            dbc.Label("Mode", className="fw-bold"),
            dbc.RadioItems(id=f"{p}-mode", options=_MODE_OPTIONS, value="quick"),
            html.Ul(
                [
                    html.Li("Quick test runs into a separate test table — iterate the prompt freely without touching real data.", className="small"),
                    html.Li(["Full calibration writes real AI decisions + a sample; review it in ", html.Strong("Screening → status ‘Calibration sample’"), " to get κ."], className="small"),
                ],
                className="mt-1",
            ),
        ]
    else:
        default_n = min(3, project.config.extraction.calibration.min)
        intro = "Run the extraction prompt on a few papers (with markdown) and eyeball the extracted fields + the derived full-text decision. Results go to an isolated test table."
        mode_block = []

    return html.Div(
        [
            html.P(intro, className="text-muted small"),
            *mode_block,
            html.Div(
                dbc.RadioItems(
                    id=f"{p}-selmode",
                    options=[
                        {"label": "Random sample", "value": "random"},
                        {"label": "Pick specific papers", "value": "pick"},
                    ],
                    value="random",
                    inline=True,
                ),
                id=f"{p}-selmode-wrap",
                className="small mt-1",
            ),
            html.Div(
                [
                    dbc.Label("Papers — search by author / title / DOI / id", className="small fw-bold"),
                    dcc.Dropdown(
                        id=f"{p}-pick",
                        options=_pick_options(_test_stage(stage)),
                        multi=True,
                        placeholder="Type to search; pick one or more papers…",
                    ),
                ],
                id=f"{p}-pick-col",
                style={"display": "none"},
                className="mt-1",
            ),
            dbc.Row(
                [
                    dbc.Col([dbc.Label("Sample size (N)", className="small fw-bold"),
                             dbc.Input(id=f"{p}-n", type="number", min=1, step=1, value=default_n, size="sm")],
                            id=f"{p}-n-col", width=3),
                    dbc.Col([dbc.Label(" ", className="small d-block"),
                             dbc.Switch(id=f"{p}-mock", label="Mock (no API cost)", value=True, className="small")], width=3),
                    dbc.Col([dbc.Label(" ", className="small d-block"),
                             dbc.Button("Run", id=f"{p}-run", color="primary", size="sm")], width="auto"),
                    dbc.Col([dbc.Label(" ", className="small d-block"),
                             dbc.Button("Refresh results", id=f"{p}-refresh-btn", color="secondary", outline=True, size="sm")], width="auto"),
                ],
                className="g-2 align-items-end mt-1",
            ),
            html.Div(id=f"{p}-status", className="small mt-2"),
            dcc.Interval(id=f"{p}-poll", interval=1200, disabled=True),
            html.Hr(className="my-3"),
            html.Div(
                dbc.Row(
                    [
                        dbc.Col(dbc.Select(id=f"{p}-run-select", options=_run_options(_test_stage(stage)), size="sm"), width=6),
                        dbc.Col(dbc.Button("Clear test runs", id=f"{p}-clear", color="link", size="sm", className="text-danger p-0"), width="auto"),
                    ],
                    className="align-items-center",
                ),
                id=f"{p}-runs-bar",
            ),
            html.Div(id=f"{p}-results", className="mt-2"),
            dcc.Store(id=f"{p}-refresh", data={"ts": 0}),
        ]
    )


def register_callbacks(app: Any, stage: str = "abstract") -> None:
    p = _prefix(stage)
    test_stage = _test_stage(stage)

    if stage == "abstract":
        @app.callback(
            Output(f"{p}-pick-col", "style"),
            Output(f"{p}-n-col", "style"),
            Output(f"{p}-selmode-wrap", "style"),
            Input(f"{p}-mode", "value"),
            Input(f"{p}-selmode", "value"),
        )
        def _toggle_sel(mode, selmode):
            # Full calibration always uses a random sample (κ needs a representative draw),
            # so the pick controls are hidden there.
            if mode == "full":
                return {"display": "none"}, {}, {"display": "none"}
            if selmode == "pick":
                return {}, {"display": "none"}, {}
            return {"display": "none"}, {}, {}

        @app.callback(
            Output(f"{p}-poll", "disabled"),
            Output(f"{p}-status", "children"),
            Input(f"{p}-run", "n_clicks"),
            State(f"{p}-mode", "value"),
            State(f"{p}-n", "value"),
            State(f"{p}-mock", "value"),
            State(f"{p}-selmode", "value"),
            State(f"{p}-pick", "value"),
            prevent_initial_call=True,
        )
        def _run(n, mode, sample_n, mock, selmode, picked):
            if not n:
                return no_update, no_update
            if mode != "full" and selmode == "pick":
                ids = _picked_ids(picked)
                if not ids:
                    return no_update, dbc.Alert("Pick at least one paper.", color="warning", className="py-1 mb-0")
                started = ai_runner.start_quick_test(get_project(), 0, bool(mock), stage="abstract", source_ids=ids)
                return False, _running_alert(started)
            try:
                sample_n = max(1, int(sample_n))
            except (TypeError, ValueError):
                return no_update, _bad_n_alert()
            if mode == "full":
                started = ai_runner.start_calibration(get_project(), sample_n, bool(mock))
            else:
                started = ai_runner.start_quick_test(get_project(), sample_n, bool(mock), stage="abstract")
            return False, _running_alert(started)

        @app.callback(
            Output(f"{p}-status", "children", allow_duplicate=True),
            Output(f"{p}-poll", "disabled", allow_duplicate=True),
            Output(f"{p}-refresh", "data", allow_duplicate=True),
            Input(f"{p}-poll", "n_intervals"),
            State(f"{p}-mode", "value"),
            prevent_initial_call=True,
        )
        def _poll(_n, mode):
            key = "calibration-abstract" if mode == "full" else "quicktest-abstract"
            return _poll_common(key)

        @app.callback(
            Output(f"{p}-runs-bar", "style"),
            Output(f"{p}-results", "children"),
            Input(f"{p}-mode", "value"),
            Input(f"{p}-run-select", "value"),
            Input(f"{p}-refresh", "data"),
        )
        def _render(mode, run_value, _refresh):
            if mode == "full":
                return {"display": "none"}, _render_full()
            return {}, _render_quick_screening(run_value)
    else:
        @app.callback(
            Output(f"{p}-pick-col", "style"),
            Output(f"{p}-n-col", "style"),
            Input(f"{p}-selmode", "value"),
        )
        def _toggle_sel(selmode):
            if selmode == "pick":
                return {}, {"display": "none"}
            return {"display": "none"}, {}

        @app.callback(
            Output(f"{p}-poll", "disabled"),
            Output(f"{p}-status", "children"),
            Input(f"{p}-run", "n_clicks"),
            State(f"{p}-n", "value"),
            State(f"{p}-mock", "value"),
            State(f"{p}-selmode", "value"),
            State(f"{p}-pick", "value"),
            prevent_initial_call=True,
        )
        def _run(n, sample_n, mock, selmode, picked):
            if not n:
                return no_update, no_update
            if selmode == "pick":
                ids = _picked_ids(picked)
                if not ids:
                    return no_update, dbc.Alert("Pick at least one paper.", color="warning", className="py-1 mb-0")
                started = ai_runner.start_quick_test(get_project(), 0, bool(mock), stage="extraction", source_ids=ids)
                return False, _running_alert(started)
            try:
                sample_n = max(1, int(sample_n))
            except (TypeError, ValueError):
                return no_update, _bad_n_alert()
            started = ai_runner.start_quick_test(get_project(), sample_n, bool(mock), stage="extraction")
            return False, _running_alert(started)

        @app.callback(
            Output(f"{p}-status", "children", allow_duplicate=True),
            Output(f"{p}-poll", "disabled", allow_duplicate=True),
            Output(f"{p}-refresh", "data", allow_duplicate=True),
            Input(f"{p}-poll", "n_intervals"),
            prevent_initial_call=True,
        )
        def _poll(_n):
            return _poll_common("quicktest-extraction")

        @app.callback(
            Output(f"{p}-results", "children"),
            Input(f"{p}-run-select", "value"),
            Input(f"{p}-refresh", "data"),
        )
        def _render(run_value, _refresh):
            return _render_quick_extraction(run_value)

    @app.callback(
        Output(f"{p}-run-select", "options"),
        Output(f"{p}-run-select", "value"),
        Input(f"{p}-refresh", "data"),
        State(f"{p}-run-select", "value"),
        prevent_initial_call=True,
    )
    def _populate_runs(_refresh, current):
        opts = _run_options(test_stage)
        value = current if current and any(o["value"] == current for o in opts) else (opts[0]["value"] if opts else None)
        return opts, value

    @app.callback(
        Output(f"{p}-refresh", "data", allow_duplicate=True),
        Input(f"{p}-clear", "n_clicks"),
        prevent_initial_call=True,
    )
    def _clear(n):
        if not n:
            return no_update
        get_project().db.clear_test_runs(get_project().project_id, test_stage)
        import time as _t
        return {"ts": _t.time()}

    @app.callback(
        Output(f"{p}-refresh", "data", allow_duplicate=True),
        Input(f"{p}-refresh-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def _manual_refresh(n):
        if not n:
            return no_update
        import time as _t
        return {"ts": _t.time()}


def _poll_common(key: str):
    st = ai_runner.get_status(key)
    if st.get("running"):
        done, total = st.get("done", 0), st.get("total", 0)
        pct = int(done / total * 100) if total else 0
        bar = dbc.Progress(value=pct, label=f"{done}/{total}", striped=True, animated=True, className="mt-1")
        return html.Div(["Running…", bar]), False, no_update
    if st.get("error"):
        return dbc.Alert(f"Failed: {st['error']}", color="danger", className="py-1 mb-0"), True, no_update
    if st.get("started") and st.get("summary"):
        import time as _t
        return dbc.Alert(st["summary"], color="success", className="py-1 mb-0"), True, {"ts": _t.time()}
    return no_update, True, no_update


def _author_year(d: dict) -> str:
    ay = d["authors"][0].split(",")[0].strip() if d.get("authors") else ""
    return f"{ay} {d['year']}".strip() if d.get("year") else ay


def _render_quick_screening(run_value: Any) -> Any:
    project = get_project()
    runs = project.db.list_test_runs(project.project_id, "abstract")
    if not runs:
        return dbc.Alert("No test runs yet. Set N and click Run.", color="info")
    try:
        run_id = int(run_value) if run_value else runs[0]["id"]
    except (TypeError, ValueError):
        run_id = runs[0]["id"]

    decisions = project.db.list_test_decisions(run_id)
    counts = {"include": 0, "exclude": 0, "uncertain": 0}
    for d in decisions:
        if d["decision"] in counts:
            counts[d["decision"]] += 1
    header = html.Div(
        [dbc.Badge(f"{k} {v}", color=_DECISION_COLOR[k], className="me-1") for k, v in counts.items()],
        className="mb-2",
    )
    rows = []
    for d in decisions:
        conf = f"{d['confidence']:.2f}" if d.get("confidence") is not None else "—"
        rows.append(
            html.Tr([
                html.Td(f"#{d['source_id']}", className="text-muted"),
                html.Td([html.Div(_author_year(d), className="fw-bold small"), html.Div(d.get("title") or "", className="small text-muted")]),
                html.Td(dbc.Badge((d["decision"] or "").upper(), color=_DECISION_COLOR.get(d["decision"], "secondary"))),
                html.Td(conf, className="small"),
                html.Td(html.Span(d.get("reasoning") or "", className="small")),
            ])
        )
    table = dbc.Table(
        [html.Thead(html.Tr([html.Th("ID"), html.Th("Study"), html.Th("AI"), html.Th("Conf."), html.Th("Reasoning")])),
         html.Tbody(rows)],
        bordered=False, hover=True, responsive=True, size="sm",
    )
    return html.Div([header, table])


def _render_quick_extraction(run_value: Any) -> Any:
    project = get_project()
    runs = project.db.list_test_runs(project.project_id, "extraction")
    if not runs:
        return dbc.Alert("No test runs yet. Set N and click Run. (Needs papers with markdown — preprocess PDFs first.)", color="info")
    try:
        run_id = int(run_value) if run_value else runs[0]["id"]
    except (TypeError, ValueError):
        run_id = runs[0]["id"]

    extractions = project.db.list_test_extractions(run_id)
    if not extractions:
        return dbc.Alert("This run produced no extractions (no papers with markdown available?).", color="warning")

    cards = []
    for ex in extractions:
        dec = ex.get("full_text_decision")
        field_rows = []
        for f in ex.get("fields", []):
            val = f.get("value")
            quote = f.get("quote")
            field_rows.append(
                html.Tr([
                    html.Td(f.get("field"), className="fw-bold small", style={"whiteSpace": "nowrap"}),
                    html.Td([
                        html.Div(str(val) if val is not None else "—", className="small"),
                        html.Div(f"“{quote}”", className="text-muted small fst-italic") if quote else None,
                    ]),
                ])
            )
        body = dbc.Table([html.Tbody(field_rows)], borderless=True, size="sm") if field_rows else html.P("(no fields)", className="text-muted small")
        cards.append(
            dbc.Card(
                dbc.CardBody([
                    html.Div([
                        html.Strong(f"#{ex['source_id']} ", className="me-2"),
                        html.Span(_author_year(ex), className="text-muted me-2"),
                        dbc.Badge(("full-text: " + dec.upper()) if dec else "no decision",
                                  color=_DECISION_COLOR.get(dec, "secondary")),
                    ], className="mb-1"),
                    html.Div(ex.get("title") or "", className="small text-muted mb-2"),
                    body,
                ]),
                className="mb-2",
            )
        )
    return html.Div(cards)


def _render_full() -> Any:
    from ailr.tasks.calibrate import sample_agreement

    project = get_project()
    rounds = project.db.list_calibration_rounds(project.project_id, "screening")
    if not rounds:
        return dbc.Alert("No calibration sample yet. Set N and click Run.", color="info")

    latest = max(rounds)
    sample = project.db.list_calibration_sample(project.project_id, "screening", latest)
    sample_ids = [s.id for s in sample if s.id is not None]
    ag = sample_agreement(project, sample_ids)

    kappa = "—" if ag["kappa"] != ag["kappa"] else f"{ag['kappa']:.3f}"
    agreement = "—" if ag["agreement"] != ag["agreement"] else f"{ag['agreement'] * 100:.0f}%"
    target = project.config.screening.target_kappa

    panel = dbc.Row(
        [
            dbc.Col(_stat_card(f"Sample (round {latest})", str(len(sample_ids))), width=3),
            dbc.Col(_stat_card("AI + human paired", str(ag["paired_count"])), width=3),
            dbc.Col(_stat_card(f"Cohen's κ (target {target})", kappa), width=3),
            dbc.Col(_stat_card("Agreement", agreement), width=3),
        ],
        className="g-2",
    )
    if ag["paired_count"] == 0:
        note = dbc.Alert(
            ["AI is done. Now review these ", html.Strong(str(len(sample_ids))),
             " in ", html.Strong("Screening → status ‘Calibration sample’"), " to get κ."],
            color="info", className="mt-3 mb-0",
        )
        return html.Div([panel, note])

    dis_rows = [
        html.Tr([
            html.Td(f"#{d['source_id']}", className="text-muted"),
            html.Td(dbc.Badge(d["ai"].upper(), color=_DECISION_COLOR.get(d["ai"], "secondary"))),
            html.Td(dbc.Badge(d["human"].upper(), color=_DECISION_COLOR.get(d["human"], "secondary"))),
        ])
        for d in ag["disagreements"]
    ]
    dis = html.Div([
        html.H6("Disagreements", className="mt-3"),
        dbc.Table([html.Thead(html.Tr([html.Th("ID"), html.Th("AI"), html.Th("Human")])), html.Tbody(dis_rows)],
                  bordered=False, hover=True, size="sm") if dis_rows
        else html.P("No disagreements on paired sources.", className="text-muted small"),
    ])
    return html.Div([panel, dis])


def _stat_card(label: str, value: str) -> Any:
    return dbc.Card(
        dbc.CardBody([html.Div(value, className="h4 mb-0"), html.Div(label, className="text-muted small")]),
        className="text-center",
    )
