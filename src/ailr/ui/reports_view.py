"""Reports tab: a visual PRISMA flow diagram + previewable, downloadable exports."""

import json
from typing import Any

import dash_bootstrap_components as dbc
from dash import Input, Output, State, ctx, dcc, html, no_update

from ailr.metrics import (
    BINARY_CATEGORIES,
    THREE_WAY_CATEGORIES,
    binarize,
    cohen_kappa,
    cohen_kappa_ci,
    confusion_matrix,
    decisions_for_pair,
    pabak,
    percent_agreement,
    rater_overlaps,
)
from ailr.ui._project import get_project


_IRR_STAGE_OPTIONS = [
    {"label": "Abstract screening", "value": "abstract"},
    {"label": "Full-text review", "value": "full_text"},
]
_IRR_CATS_OPTIONS = [
    {"label": "Binary — uncertain counts as include", "value": "binary"},
    {"label": "Three-way — include / exclude / uncertain", "value": "three"},
]


def _confusion_block(pairs: list, categories: list[str], rater_a: str, rater_b: str) -> Any:
    if not pairs:
        return html.Small("(no shared records)", className="text-muted")
    cats, matrix = confusion_matrix(pairs, categories=categories)
    head = html.Thead(html.Tr([html.Th(f"{rater_a} ↓ / {rater_b} →")] + [html.Th(c) for c in cats]))
    body = html.Tbody([
        html.Tr([html.Th(c)] + [html.Td(matrix[i][j]) for j in range(len(cats))])
        for i, c in enumerate(cats)
    ])
    return dbc.Table([head, body], bordered=True, size="sm", style={"maxWidth": "440px"})


def _api_block(rows: list) -> Any:
    if not rows:
        return html.Small("(no API calls logged — Mock runs make no real calls)", className="text-muted")
    head = html.Thead(html.Tr([html.Th(h) for h in ["Provider / Model", "Calls", "Input tok", "Output tok", "Avg latency (ms)"]]))
    body = html.Tbody([
        html.Tr([
            html.Td(f"{r['provider']}/{r['model']}"),
            html.Td(r.get("calls") or 0),
            html.Td(r.get("input_tokens") or 0),
            html.Td(r.get("output_tokens") or 0),
            html.Td(f"{(r.get('avg_latency_ms') or 0):.0f}"),
        ])
        for r in rows
    ])
    return dbc.Table([head, body], bordered=False, hover=True, size="sm")


def _round(x: float) -> Any:
    return None if x != x else round(x, 3)      # x != x catches NaN


def _round_ci(ci: tuple[float, float]) -> Any:
    lo, hi = ci
    return None if lo != lo or hi != hi else [round(lo, 3), round(hi, 3)]


def _reliability(pairs: list, categories: list[str]) -> dict:
    return {
        "n_pairs": len(pairs),
        "cohen_kappa": _round(cohen_kappa(pairs, categories=categories)),
        "cohen_kappa_ci": _round_ci(cohen_kappa_ci(pairs, categories=categories)),
        "pabak": _round(pabak(pairs, categories=categories)),
        "percent_agreement": _round(percent_agreement(pairs)),
    }


def _pair_key(rater_a: str, rater_b: str) -> str:
    return json.dumps([rater_a, rater_b])


def _pair_options(rows: list) -> list[dict]:
    return [
        {"label": f"{a}  vs  {b}   ({n} shared)", "value": _pair_key(a, b)}
        for a, b, n in rater_overlaps(rows)
    ]


def _reliability_body(rows: list, pair_value: Any, cats_mode: str) -> Any:
    if not pair_value:
        return html.Small(
            "No records judged by two reviewers at this stage yet. Agreement needs the same record "
            "decided by two raters (AI + human, or two humans).",
            className="text-muted",
        )
    rater_a, rater_b = json.loads(pair_value)
    raw = decisions_for_pair(rows, rater_a, rater_b)
    if cats_mode == "binary":
        pairs, categories = binarize(raw), BINARY_CATEGORIES
    else:
        pairs, categories = raw, THREE_WAY_CATEGORIES
    rel = _reliability(pairs, categories)

    def _stat(label: str, value: Any, suffix: str = "") -> Any:
        shown = "n/a" if value is None else f"{value}{suffix}"
        return html.Span([html.Span(f"{label}: ", className="text-muted"), html.Strong(shown)], className="me-4")

    pct = rel["percent_agreement"]
    kappa_shown = rel["cohen_kappa"]
    ci = rel["cohen_kappa_ci"]
    if kappa_shown is not None and ci:
        kappa_shown = f"{kappa_shown}  [{ci[0]}, {ci[1]}]"
    return html.Div(
        [
            html.Div(
                [
                    _stat("Records judged by both", rel["n_pairs"]),
                    _stat("Cohen's κ (95% CI)", kappa_shown),
                    _stat("PABAK", rel["pabak"]),
                    _stat("% agreement", None if pct is None else round(pct * 100, 1), "%"),
                ],
                className="small mb-2",
            ),
            html.Small(
                "PABAK is the prevalence-adjusted form: when almost everything is excluded, κ can look "
                "poor even at high agreement, and PABAK shows that. The κ interval is the "
                "Fleiss-Cohen-Everitt asymptotic one; on few paired records it can run past ±1.",
                className="text-muted d-block mb-3",
            ),
            html.H6("Confusion matrix", className="mt-3"),
            _confusion_block(pairs, categories, rater_a, rater_b),
        ]
    )


def _metrics_json(proj: Any) -> str:
    db, pid = proj.db, proj.project_id
    agreement: dict[str, list] = {}
    for stage in ("abstract", "full_text"):
        rows = db.latest_decisions_by_rater(pid, stage)
        agreement[stage] = [
            {
                "rater_a": a,
                "rater_b": b,
                "binary": _reliability(binarize(decisions_for_pair(rows, a, b)), BINARY_CATEGORIES),
                "three_way": _reliability(decisions_for_pair(rows, a, b), THREE_WAY_CATEGORIES),
            }
            for a, b, _ in rater_overlaps(rows)
        ]
    payload = {
        "screening": {
            "ai": db.screening_summary(pid, "ai"),
            "human": db.screening_summary(pid, "human"),
        },
        "agreement": agreement,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)

_MAIN_BOX = {
    "border": "1px solid #c8c8c8",
    "borderRadius": "6px",
    "padding": "12px 16px",
    "background": "#ffffff",
}
_SIDE_BOX = {
    "border": "1px dashed #bcbcbc",
    "borderRadius": "6px",
    "padding": "12px 16px",
    "background": "#fafafa",
}


def _box(lead: str, label: str, style: dict) -> Any:
    return html.Div(
        [html.Strong(lead, className="me-1"), html.Span(label)],
        style=style,
    )


def _down_arrow() -> Any:
    return html.Div("↓", style={"textAlign": "center", "fontSize": "1.4rem", "color": "#888", "lineHeight": "1.2"})


def _stage_row(main: Any, side: Any) -> Any:
    return dbc.Row(
        [
            dbc.Col(main, width=6),
            dbc.Col(
                html.Div(["→  ", side] if side else "", className="d-flex align-items-center")
                if side else "",
                width=6,
            ),
        ],
        className="align-items-center",
    )


def _identification_box(c: dict) -> Any:
    two_arms = c["other_arm"]["identified"] > 0
    n = c["database_arm"]["identified"] if two_arms else c["records_identified"]
    listed = c["by_route"].get("database", []) if two_arms else c["by_source_database"]
    children: list[Any] = []
    if two_arms:
        children.append(html.Div("Via databases and registers", className="small text-muted"))
    children.append(html.Div([html.Strong(f"{n} ", className="me-1"), "records identified"]))
    if listed:
        children.append(
            html.Ul(
                [html.Li(f"{d['source_database']}: {d['n']}", className="small") for d in listed],
                className="mb-0 mt-1",
            )
        )
    return html.Div(children, style=_MAIN_BOX)


def _other_arm_block(c: dict) -> Any:
    """PRISMA 2020's second identification arm, shown only when something was found that way."""
    other = c["other_arm"]
    if not other["identified"]:
        return None
    rows = [
        ("Records identified", other["identified"]),
        ("Reports sought for retrieval", other["sought"]),
        ("Reports assessed for eligibility", other["assessed"]),
        ("Studies included", other["included"]),
    ]
    return html.Div(
        [
            html.Div("Identified via other methods", className="fw-bold mb-1"),
            html.Ul(
                [html.Li(f"{d['source_database']}: {d['n']}", className="small") for d in c["by_route"].get("other", [])],
                className="mb-2 mt-0",
            ),
            dbc.Table(
                [html.Tbody([html.Tr([html.Td(label), html.Td(html.Strong(str(n)))]) for label, n in rows])],
                bordered=False, size="sm", className="mb-0",
            ),
            html.Small(
                "Citation searching, hand searching, and grey literature are reported as a separate "
                "arm; both arms feed the same included set.",
                className="text-muted",
            ),
        ],
        style=_MAIN_BOX,
        className="mt-3",
    )


def _prisma_diagram(db: Any, pid: int, c: dict) -> Any:
    ft_excl_counts = db.full_text_exclusion_counts(pid)

    dup_side = _box(f"{c['duplicates_removed']}", "duplicates removed before screening", _SIDE_BOX) if c["duplicates_removed"] else None
    abs_side = _box(f"{c['abstract_excluded']}", "studies excluded at title/abstract", _SIDE_BOX)
    retrieval_side = _box(f"{c['reports_not_retrieved']}", "reports not retrieved (no full text)", _SIDE_BOX) if c["reports_not_retrieved"] else None

    ft_side_children: list[Any] = [html.Div([html.Strong(f"{c['full_text_excluded_reports']} "), "studies excluded, with reasons:"])]
    if ft_excl_counts:
        ft_side_children.append(
            html.Ul([html.Li(f"{r['reason']}: {r['n']}", className="small") for r in ft_excl_counts], className="mb-0 mt-1")
        )
    ft_side = html.Div(ft_side_children, style=_SIDE_BOX)

    return html.Div(
        [
            _stage_row(_identification_box(c), dup_side),
            dbc.Row(dbc.Col(_down_arrow(), width=6)),
            _stage_row(_box(f"{c['records_after_dedup']}", "records after duplicates removed", _MAIN_BOX), abs_side),
            dbc.Row(dbc.Col(_down_arrow(), width=6)),
            _stage_row(_box(f"{c['reports_sought']}", "reports sought for retrieval", _MAIN_BOX), retrieval_side),
            dbc.Row(dbc.Col(_down_arrow(), width=6)),
            _stage_row(_box(f"{c['full_text_assessed']}", "full-text studies assessed for eligibility", _MAIN_BOX), ft_side),
            dbc.Row(dbc.Col(_down_arrow(), width=6)),
            _stage_row(_box(f"{c['studies_included']}", "studies included", _MAIN_BOX), None),
            _other_arm_block(c),
        ]
    )


def layout() -> Any:
    project = get_project()
    db = project.db
    pid = project.project_id

    from ailr.exports.methods import build_methods_skeleton
    from ailr.exports.prisma import prisma_counts

    counts = prisma_counts(project)
    api_summary = db.api_call_summary(pid)
    methods_text = build_methods_skeleton(project, counts=counts, api_summary=api_summary)

    prisma_block = [
        html.H4("PRISMA flow"),
        html.P("Auto-generated from your decisions. AI and human reviewers are reported separately.", className="text-muted small"),
        _prisma_diagram(db, pid, counts),
        html.Div(
            dbc.ButtonGroup(
                [
                    dbc.Button("Download PRISMA (MD)", id="report-dl-prisma", color="primary", outline=True, size="sm"),
                    dbc.Button("Download PRISMA (SVG)", id="report-dl-svg", color="primary", outline=True, size="sm"),
                ]
            ),
            className="mt-3",
        ),
        html.Hr(className="my-4"),
        html.H4("Methods skeleton"),
        html.P("Draft methods paragraph — edit to fit your journal.", className="text-muted small"),
        html.Div(
            dcc.Markdown(methods_text),
            style={"maxHeight": "300px", "overflow": "auto", "border": "1px solid #eee", "borderRadius": "6px", "padding": "12px"},
        ),
        html.Div(
            dbc.Button("Download methods (MD)", id="report-dl-methods", color="primary", outline=True, size="sm"),
            className="mt-2",
        ),
    ]

    irr_rows = db.latest_decisions_by_rater(pid, "abstract")
    irr_options = _pair_options(irr_rows)
    irr_default = irr_options[0]["value"] if irr_options else None

    metrics_block = [
        html.H4("Inter-rater reliability (screening)"),
        html.P(
            "Agreement between any two reviewers on the records they both judged. Votes are read as "
            "cast, before adjudication, so resolving a conflict does not change these numbers.",
            className="text-muted small",
        ),
        dbc.Row(
            [
                dbc.Col([dbc.Label("Stage", className="small mb-0"),
                         dbc.Select(id="report-irr-stage", options=_IRR_STAGE_OPTIONS, value="abstract", size="sm")], width="auto"),
                dbc.Col([dbc.Label("Reviewer pair", className="small mb-0"),
                         dbc.Select(id="report-irr-pair", options=irr_options, value=irr_default, size="sm")], width=4),
                dbc.Col([dbc.Label("Categories", className="small mb-0"),
                         dbc.Select(id="report-irr-cats", options=_IRR_CATS_OPTIONS, value="binary", size="sm")], width="auto"),
            ],
            className="g-2 align-items-end mb-3",
        ),
        html.Div(_reliability_body(irr_rows, irr_default, "binary"), id="report-irr-body"),
        html.Div(
            dbc.Button("Download the votes behind this (CSV)", id="report-dl-pairs", color="link", size="sm", className="p-0 mt-3"),
        ),
        html.Hr(className="my-4"),
        html.H4("Quote audit (extraction)"),
        html.P(
            "Checks every AI-extracted quote verbatim against its paper's markdown: how many values "
            "carry a quote, and how many quotes are actually in the text. \"Not found\" is a list to "
            "spot-check, not a hallucination verdict — PDF conversion artifacts cause some misses.",
            className="text-muted small",
        ),
        dbc.Button("Run quote audit", id="report-quoteaudit-run", color="primary", outline=True, size="sm"),
        dcc.Loading(html.Div(id="report-quoteaudit-body", className="mt-2")),
        dcc.Store(id="report-quoteaudit-misses"),
        html.Hr(className="my-4"),
        html.H4("API usage"),
        html.P("Per provider/model tokens + latency. Multiply by your provider's current rates for spend; "
               "ailr does not ship a price table. Mock runs make no real calls.", className="text-muted small"),
        _api_block(api_summary),
    ]

    exports_block = [
        html.H4("Data exports"),
        dbc.ButtonGroup(
            [
                dbc.Button("Extraction — AI (CSV)", id="report-dl-csv", color="primary", outline=True),
                dbc.Button("Extraction — final (CSV)", id="report-dl-csv-human", color="primary", outline=True),
                dbc.Button("Extraction — AI (JSON)", id="report-dl-json", color="primary", outline=True),
                dbc.Button("Extraction — AI (per-paper JSON, ZIP)", id="report-dl-json-zip", color="primary", outline=True),
                dbc.Button("RIS of includes", id="report-dl-ris", color="primary", outline=True),
                dbc.Button("Screening metrics (JSON)", id="report-dl-metrics", color="primary", outline=True),
            ]
        ),
    ]

    tabs = dbc.Tabs(
        [
            dbc.Tab(html.Div(prisma_block, className="mt-3"), label="PRISMA & methods", tab_id="report-tab-prisma"),
            dbc.Tab(html.Div(metrics_block, className="mt-3"), label="Reliability & API", tab_id="report-tab-metrics"),
            dbc.Tab(html.Div(exports_block, className="mt-3"), label="Data exports", tab_id="report-tab-exports"),
        ],
        active_tab="report-tab-prisma",
        className="mt-2",
    )
    return html.Div(
        [
            html.H4("Reports"),
            tabs,
            html.Div(id="report-dl-feedback", className="small text-muted mt-2"),
            dcc.Download(id="report-download"),
        ]
    )


def register_callbacks(app: Any) -> None:
    # Both callbacks below live entirely inside the Reports tab (own Inputs, own Outputs), so a
    # global store ticking on another tab can never fire them against a missing component.
    @app.callback(
        Output("report-quoteaudit-body", "children"),
        Output("report-quoteaudit-misses", "data"),
        Input("report-quoteaudit-run", "n_clicks"),
        prevent_initial_call=True,
    )
    def _quote_audit(n):
        if not n:
            return no_update, no_update
        from ailr.quote_audit import audit_project_ai

        project = get_project()
        audit, audited, skipped = audit_project_ai(project)
        if audit.values == 0:
            return dbc.Alert("No AI extractions with markdown to audit yet.", color="info", className="py-2"), None

        headline = html.Div([
            html.Strong(f"{audited} paper(s) audited"),
            html.Span(f" ({skipped} skipped — markdown file missing)", className="text-muted") if skipped else None,
            html.Div(
                f"coverage: {audit.quoted}/{audit.values} values quoted ({audit.coverage:.0%})  •  "
                f"verbatim: {audit.verbatim}/{audit.checked} ({audit.verbatim_rate:.0%})"
                if audit.checked else
                f"coverage: {audit.quoted}/{audit.values} values quoted ({audit.coverage:.0%})",
            ),
        ], className="small mb-2")

        rows = [
            html.Tr([
                html.Td(name, className="fw-bold"),
                html.Td(f"{s[1]}/{s[0]}"), html.Td(f"{s[1] / s[0]:.0%}"),
                html.Td(f"{s[3]}/{s[2]}" if s[2] else "—"),
            ])
            for name, s in sorted(audit.per_field.items(), key=lambda kv: (kv[1][1] / kv[1][0], kv[0]))
        ]
        table = dbc.Table(
            [html.Thead(html.Tr([html.Th("field"), html.Th("quoted/values"), html.Th("coverage"), html.Th("verbatim/quotes")])),
             html.Tbody(rows)],
            size="sm", striped=True, style={"fontSize": "0.78rem"},
        )
        dl_btn = dbc.Button(
            f"Download quotes not found ({len(audit.not_found)}, CSV)",
            id="report-quoteaudit-dl", color="link", size="sm", className="p-0",
        ) if audit.not_found else html.Small("Every quote was found verbatim.", className="text-success")
        return html.Div([headline, table, dl_btn]), audit.not_found

    @app.callback(
        Output("report-download", "data", allow_duplicate=True),
        Input("report-quoteaudit-dl", "n_clicks"),
        State("report-quoteaudit-misses", "data"),
        prevent_initial_call=True,
    )
    def _quote_audit_download(n, misses):
        if not n or not misses:
            return no_update
        import csv
        import io

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["source_id", "field", "quote"])
        for m in misses:
            w.writerow([m.get("source_id"), m.get("field"), m.get("quote")])
        return dict(content=buf.getvalue(), filename="quote_audit_not_found.csv")

    @app.callback(
        Output("report-irr-pair", "options"),
        Output("report-irr-pair", "value"),
        Input("report-irr-stage", "value"),
    )
    def _irr_pairs(stage):
        proj = get_project()
        options = _pair_options(proj.db.latest_decisions_by_rater(proj.project_id, stage or "abstract"))
        return options, (options[0]["value"] if options else None)

    @app.callback(
        Output("report-irr-body", "children"),
        Input("report-irr-stage", "value"),
        Input("report-irr-pair", "value"),
        Input("report-irr-cats", "value"),
    )
    def _irr_body(stage, pair_value, cats_mode):
        proj = get_project()
        rows = proj.db.latest_decisions_by_rater(proj.project_id, stage or "abstract")
        return _reliability_body(rows, pair_value, cats_mode or "binary")

    @app.callback(
        Output("report-download", "data"),
        Output("report-dl-feedback", "children"),
        Input("report-dl-csv", "n_clicks"),
        Input("report-dl-prisma", "n_clicks"),
        Input("report-dl-svg", "n_clicks"),
        Input("report-dl-methods", "n_clicks"),
        Input("report-dl-ris", "n_clicks"),
        Input("report-dl-csv-human", "n_clicks"),
        Input("report-dl-json", "n_clicks"),
        Input("report-dl-json-zip", "n_clicks"),
        Input("report-dl-metrics", "n_clicks"),
        Input("report-dl-pairs", "n_clicks"),
        State("report-irr-stage", "value"),
        prevent_initial_call=True,
    )
    def _download(_c, _p, _s, _m, _r, _ch, _j, _jz, _mx, _pairs, irr_stage):
        trig = ctx.triggered_id
        if not any(t.get("value") for t in (ctx.triggered or [])):
            return no_update, no_update

        from ailr.exports.methods import build_methods_skeleton
        from ailr.exports.prisma import build_prisma_report, build_prisma_svg
        from ailr.exports.reliability import screening_decisions_csv
        from ailr.exports.ris import export_includes_ris
        from ailr.exports.tables import extraction_table_csv, extraction_table_json, extraction_per_paper_zip

        proj = get_project()
        name = (proj.config.project.name or "review").replace(" ", "_")
        stage = irr_stage or "abstract"

        # Per-paper JSON is delivered as a ZIP (binary), so it uses send_bytes rather than send_string.
        if trig == "report-dl-json-zip":
            fn = f"{name}_extraction_ai_per_paper.zip"
            try:
                data = extraction_per_paper_zip(proj, extractor_type="ai", only_includes=True)
            except Exception as e:
                import traceback
                traceback.print_exc()
                return no_update, dbc.Alert(f"Export failed: {e}", color="danger", className="mb-0 py-1")
            return dcc.send_bytes(lambda b: b.write(data), fn), f"Downloaded {fn}"

        builders = {
            "report-dl-csv": (lambda: extraction_table_csv(proj, extractor_type="ai", only_includes=True), f"{name}_extraction_ai.csv"),
            "report-dl-csv-human": (lambda: extraction_table_csv(proj, extractor_type="final", only_includes=True), f"{name}_extraction_final.csv"),
            "report-dl-json": (lambda: extraction_table_json(proj, extractor_type="ai", only_includes=True), f"{name}_extraction_ai.json"),
            "report-dl-prisma": (lambda: build_prisma_report(proj), f"{name}_prisma.md"),
            "report-dl-svg": (lambda: build_prisma_svg(proj), f"{name}_prisma.svg"),
            "report-dl-methods": (lambda: build_methods_skeleton(proj), f"{name}_methods.md"),
            "report-dl-ris": (lambda: export_includes_ris(proj), f"{name}_includes.ris"),
            "report-dl-metrics": (lambda: _metrics_json(proj), f"{name}_metrics.json"),
            "report-dl-pairs": (lambda: screening_decisions_csv(proj, stage=stage), f"{name}_screening_votes_{stage}.csv"),
        }
        if trig not in builders:
            return no_update, no_update
        build, filename = builders[trig]
        try:
            content = build()
        except Exception as e:
            import traceback
            traceback.print_exc()
            return no_update, dbc.Alert(f"Export failed: {e}", color="danger", className="mb-0 py-1")
        msg: Any = f"Downloaded {filename}"
        if trig == "report-dl-ris":
            missing = proj.db.count_sources_missing_doi(proj.project_id)
            if missing:
                msg = dbc.Alert(
                    f"Downloaded {filename}. Note: {missing} source(s) have no DOI — after the Zotero round-trip "
                    "these may not re-link by DOI (they fall back to title matching). Add DOIs on the Sources tab.",
                    color="warning", className="mb-0 py-1",
                )
        return dcc.send_string(content, filename), msg
