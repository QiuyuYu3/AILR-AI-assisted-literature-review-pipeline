"""Reports tab: a visual PRISMA flow diagram + previewable, downloadable exports."""

import json
from typing import Any

import dash_bootstrap_components as dbc
from dash import Input, Output, ctx, dcc, html, no_update

from ailr.metrics import cohen_kappa, confusion_matrix, percent_agreement
from ailr.ui._common import get_project


def _confusion_block(pairs: list) -> Any:
    if not pairs:
        return html.Small("(no paired AI+human decisions yet)", className="text-muted")
    cats, matrix = confusion_matrix(pairs, categories=["include", "exclude", "uncertain"])
    head = html.Thead(html.Tr([html.Th("AI ↓ / Human →")] + [html.Th(c) for c in cats]))
    body = html.Tbody([
        html.Tr([html.Th(c)] + [html.Td(matrix[i][j]) for j in range(len(cats))])
        for i, c in enumerate(cats)
    ])
    return dbc.Table([head, body], bordered=True, size="sm", style={"maxWidth": "440px"})


def _api_block(rows: list) -> Any:
    if not rows:
        return html.Small("(no API calls logged — Mock runs aren't billed)", className="text-muted")
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


def _reliability(pairs: list) -> dict:
    cats = ["include", "exclude", "uncertain"]
    k = cohen_kappa(pairs, categories=cats)
    a = percent_agreement(pairs)
    return {
        "n_pairs": len(pairs),
        "cohen_kappa": None if k != k else round(k, 3),       # k != k catches NaN
        "percent_agreement": None if a != a else round(a, 3),
    }


def _reliability_block(rel: dict) -> Any:
    if rel["n_pairs"] == 0:
        return html.Small("No AI+human paired decisions yet (run AI screening + make human decisions).", className="text-muted")
    k = rel["cohen_kappa"]
    a = rel["percent_agreement"]
    return html.Div(
        [
            html.Span(f"Paired decisions: {rel['n_pairs']}", className="me-3"),
            html.Span(f"Cohen's κ: {k if k is not None else 'n/a'}", className="me-3"),
            html.Span(f"% agreement: {int(a * 100) if a is not None else 'n/a'}%"),
        ],
        className="small",
    )


def _metrics_json(proj: Any) -> str:
    db, pid = proj.db, proj.project_id
    pairs = [(p["ai_decision"], p["human_decision"]) for p in db.paired_screening_decisions(pid)]
    payload = {
        "screening": {
            "ai": db.screening_summary(pid, "ai"),
            "human": db.screening_summary(pid, "human"),
            **_reliability(pairs),
        }
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
    children: list[Any] = [
        html.Div([html.Strong(f"{c['records_identified']} ", className="me-1"), "records identified"]),
    ]
    if c["by_source_database"]:
        children.append(
            html.Ul(
                [html.Li(f"{d['source_database']}: {d['n']}", className="small") for d in c["by_source_database"]],
                className="mb-0 mt-1",
            )
        )
    return html.Div(children, style=_MAIN_BOX)


def _prisma_diagram(db: Any, pid: int, c: dict) -> Any:
    ft_excl_counts = db.full_text_exclusion_counts(pid)

    dup_side = _box(f"{c['duplicates_removed']}", "duplicates removed before screening", _SIDE_BOX) if c["duplicates_removed"] else None
    abs_side = _box(f"{c['abstract_excluded']}", "studies excluded at title/abstract", _SIDE_BOX)
    retrieval_side = _box(f"{c['reports_not_retrieved']}", "reports not retrieved (no full text)", _SIDE_BOX) if c["reports_not_retrieved"] else None

    ft_side_children: list[Any] = [html.Div([html.Strong(f"{c['full_text_excluded']} "), "studies excluded, with reasons:"])]
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
        ]
    )


def layout() -> Any:
    project = get_project()
    db = project.db
    pid = project.project_id

    from ailr.exports.methods import build_methods_skeleton
    from ailr.exports.prisma import prisma_counts

    counts = prisma_counts(project)
    paired = [(p["ai_decision"], p["human_decision"]) for p in db.paired_screening_decisions(pid)]
    api_summary = db.api_call_summary(pid)
    methods_text = build_methods_skeleton(project, counts=counts, api_summary=api_summary, pairs=paired)

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

    metrics_block = [
        html.H4("Inter-rater reliability (screening: AI vs human)"),
        _reliability_block(_reliability(paired)),
        html.H6("Confusion matrix", className="mt-3"),
        _confusion_block(paired),
        html.Hr(className="my-4"),
        html.H4("API usage"),
        html.P("Per provider/model token + cost + latency. Mock runs are not billed.", className="text-muted small"),
        _api_block(api_summary),
    ]

    exports_block = [
        html.H4("Data exports"),
        dbc.ButtonGroup(
            [
                dbc.Button("Extraction — AI (CSV)", id="report-dl-csv", color="primary", outline=True),
                dbc.Button("Extraction — final (CSV)", id="report-dl-csv-human", color="primary", outline=True),
                dbc.Button("Extraction — AI (JSON)", id="report-dl-json", color="primary", outline=True),
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
        Input("report-dl-metrics", "n_clicks"),
        prevent_initial_call=True,
    )
    def _download(_c, _p, _s, _m, _r, _ch, _j, _mx):
        trig = ctx.triggered_id
        if not any(t.get("value") for t in (ctx.triggered or [])):
            return no_update, no_update

        from ailr.exports.methods import build_methods_skeleton
        from ailr.exports.prisma import build_prisma_report, build_prisma_svg
        from ailr.exports.ris import export_includes_ris
        from ailr.exports.tables import extraction_table_csv, extraction_table_json

        proj = get_project()
        name = (proj.config.project.name or "review").replace(" ", "_")

        builders = {
            "report-dl-csv": (lambda: extraction_table_csv(proj, extractor_type="ai", only_includes=True), f"{name}_extraction_ai.csv"),
            "report-dl-csv-human": (lambda: extraction_table_csv(proj, extractor_type="human", only_includes=True), f"{name}_extraction_final.csv"),
            "report-dl-json": (lambda: extraction_table_json(proj, extractor_type="ai", only_includes=True), f"{name}_extraction_ai.json"),
            "report-dl-prisma": (lambda: build_prisma_report(proj), f"{name}_prisma.md"),
            "report-dl-svg": (lambda: build_prisma_svg(proj), f"{name}_prisma.svg"),
            "report-dl-methods": (lambda: build_methods_skeleton(proj), f"{name}_methods.md"),
            "report-dl-ris": (lambda: export_includes_ris(proj), f"{name}_includes.ris"),
            "report-dl-metrics": (lambda: _metrics_json(proj), f"{name}_metrics.json"),
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
