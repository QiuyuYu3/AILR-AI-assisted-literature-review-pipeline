"""Structured inclusion/exclusion criteria editor (GUI for criteria.yaml)."""

import json
from typing import Any

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update

from ailr.criteria import CriteriaSet, CriterionSpec, assign_ids, import_from_text, render_criteria_markdown, save_criteria
from ailr.ingest.criteria_import import parse_criteria_import
from ailr.ui import version_ui
from ailr.ui._common import get_project, read_criteria_set, with_help

_KIND = "criteria"

_AGENT_CRITERIA_MSG = """Help me turn my literature-review screening criteria into JSON I can import into my tool.

Review topic: [one sentence]
My criteria (paste your current criteria, notes, or protocol):
[paste here]

Return ONLY a JSON object in this exact shape (valid JSON, no comments):
{
  "criteria": [
    {"name": "Study type",
     "pass_if": "Empirical study published as a full paper (journal, conference, or preprint).",
     "fail_if": "Review, meta-analysis, thesis, or abstract without a full paper.",
     "uncertain_if": ""},
    {"name": "Real-time human interaction",
     "pass_if": "Real-time interaction between two or more humans.",
     "fail_if": "Human-AI or animal interaction, or no interactive task.",
     "uncertain_if": "The nature of the interaction is unclear from the paper."}
  ]
}
Rules:
- one entry per criterion; give each a short "name" and the conditions under which a paper PASSES and FAILS it.
- add "uncertain_if" only when full-text review is genuinely needed; otherwise leave it "".
- write the rules as plain, decidable conditions — what in the paper makes it pass or fail."""

_PREVIEW_BASE = {"whiteSpace": "pre-wrap", "fontSize": "0.75rem", "border": "1px solid #eee", "borderRadius": "6px", "padding": "8px"}
_PREVIEW_COLLAPSED = {**_PREVIEW_BASE, "maxHeight": "70vh", "overflow": "auto"}
_PREVIEW_EXPANDED = {**_PREVIEW_BASE, "overflow": "visible"}


def _mono(h: int) -> dict:
    return {"height": f"{h}px", "fontFamily": "monospace", "fontSize": "0.75rem"}


def _initial_rows() -> list[dict]:
    return [c.model_dump() for c in read_criteria_set().criteria]


def _specs(rows: list[dict]) -> list[CriterionSpec]:
    return [CriterionSpec(**{k: r.get(k, "") for k in ("id", "name", "pass_if", "fail_if", "uncertain_if")}) for r in rows]


def _write_criteria_json(root, cs) -> Any:
    """Write a re-importable {"criteria": [...]} backup next to the project. Returns Path or None."""
    p = root / "criteria.json"
    try:
        p.write_text(json.dumps({"criteria": [c.model_dump() for c in cs.criteria]}, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return None
    return p


def _to_content(rows) -> str:
    """crit-store rows -> the JSON snapshot stored as a version (IDs assigned, like a real Save)."""
    specs = _specs(rows or [])
    assign_ids(specs)
    return json.dumps({"criteria": [s.model_dump() for s in specs]}, ensure_ascii=False)


def _to_text(content: str) -> str:
    rows = json.loads(content).get("criteria", [])
    return render_criteria_markdown(CriteriaSet(criteria=_specs(rows)))


def _short(text: str, n: int = 60) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


def _list_item(i: int, c: dict) -> Any:
    summ = []
    if c.get("pass_if", "").strip():
        summ.append("PASS: " + _short(c["pass_if"], 50))
    if c.get("fail_if", "").strip():
        summ.append("FAIL: " + _short(c["fail_if"], 50))
    return html.Div(
        [
            dbc.Button("↑", id={"type": "crit-moveup", "idx": i}, size="sm", color="link", className="p-0 me-1"),
            dbc.Button("↓", id={"type": "crit-movedown", "idx": i}, size="sm", color="link", className="p-0 me-2"),
            html.Span(c.get("name") or "(unnamed)", className="fw-bold"),
            dbc.Button("Edit", id={"type": "crit-edit", "idx": i}, size="sm", color="link", className="p-0 ms-2"),
            dbc.Button("Remove", id={"type": "crit-remove", "idx": i}, size="sm", color="link", className="p-0 ms-2 text-danger"),
            html.Div(" · ".join(summ), className="text-muted small ms-4") if summ else None,
        ],
        className="mb-2",
    )


def _render_list(rows: list[dict]) -> Any:
    if not rows:
        return html.Small("No criteria yet. Add one below, or import your existing criteria.", className="text-muted")
    return [_list_item(i, c) for i, c in enumerate(rows)]


def _render_preview(rows: list[dict]) -> str:
    specs = [s for s in _specs(rows) if s.name.strip() or s.pass_if.strip() or s.fail_if.strip()]
    assign_ids(specs)
    return render_criteria_markdown(CriteriaSet(criteria=specs)) or "(nothing yet)"


def _report_view(report: Any) -> list:
    if report.has_errors:
        head = dbc.Alert(f"{len(report.errors)} error(s) to fix · {report.ok_count} OK", color="danger", className="mb-1 py-1")
    elif report.warnings:
        head = dbc.Alert(f"{report.ok_count} OK · {len(report.warnings)} warning(s)", color="warning", className="mb-1 py-1")
    else:
        head = dbc.Alert(f"{report.ok_count} criterion(s) OK", color="success", className="mb-1 py-1")
    rows = [html.Div(f"{'✕' if it.level == 'error' else '!'} {(it.field + ': ') if it.field else ''}{it.message}",
                     className="small " + ("text-danger" if it.level == "error" else "text-secondary")) for it in report.items]
    return [head, *rows]


def _passfail_fields(prefix: str, vals: dict | None = None) -> list:
    vals = vals or {}
    return [
        dbc.Label("PASS if", className="small fw-bold text-success mb-0"),
        dbc.Textarea(id=f"{prefix}-pass", value=vals.get("pass_if", ""), placeholder="What makes a paper meet this criterion", style=_mono(52), className="mb-1"),
        dbc.Label("FAIL if", className="small fw-bold text-danger mb-0"),
        dbc.Textarea(id=f"{prefix}-fail", value=vals.get("fail_if", ""), placeholder="What makes a paper fail this criterion", style=_mono(52), className="mb-1"),
        dbc.Label("UNCERTAIN if (optional)", className="small fw-bold text-muted mb-0"),
        dbc.Textarea(id=f"{prefix}-uncertain", value=vals.get("uncertain_if", ""), placeholder="When the paper is ambiguous and needs full-text review", style=_mono(40)),
    ]


def _editor_column(rows: list[dict]) -> list:
    return [
        dbc.Alert(
            [
                html.Strong("What this is. "),
                "Your screening criteria — the rules that decide whether a paper is in or out. For each "
                "criterion give a short name and the conditions under which a paper ", html.Strong("passes"),
                ", ", html.Strong("fails"), ", or is ", html.Strong("uncertain"), ". The same criteria screen "
                "abstracts and, during extraction, re-check each paper against the full text.",
            ],
            color="light", className="small py-2",
        ),
        html.H6("Criteria"),
        html.Div(id="crit-list", children=_render_list(rows)),
        html.Hr(),
        html.H6("Add a criterion"),
        dbc.Input(id="crit-add-name", placeholder="Criterion name (e.g. Study type)", size="sm", className="mb-1"),
        *_passfail_fields("crit-add"),
        html.Div(dbc.Button("Add criterion", id="crit-add", color="secondary", size="sm", className="mt-1"), className="mt-1"),
        html.Div(id="crit-add-feedback", className="small mt-1"),
        html.Hr(),
        html.Div(dbc.Button("Save criteria", id="crit-save", color="primary", size="sm")),
        html.Div(id="crit-feedback", className="small mt-2"),
        version_ui.history_layout("crit", _KIND),
        html.Hr(className="my-3"),
        html.H6("Import from existing criteria text"),
        html.P("Paste your current criteria (the 'B1 … PASS if: … FAIL if: …' format works best); it is split into rows. Review and edit, then Save.",
               className="text-muted small mb-1"),
        dbc.Textarea(id="crit-import-text", style=_mono(120)),
        dbc.Button("Load into editor", id="crit-import-load", color="secondary", outline=True, size="sm", className="mt-1 mb-1"),
        html.Details(
            [
                html.Summary("Draft / import with your AI (JSON)"),
                html.Div(
                    [
                        html.P("Have ChatGPT/Claude turn your criteria into JSON, paste it, validate, then load to review.",
                               className="text-muted small mt-2 mb-1"),
                        html.Div(
                            [
                                dbc.Label("Message to your AI", className="small fw-bold mb-0 me-2"),
                                dcc.Clipboard(target_id="crit-json-msg", title="Copy message", style={"display": "inline-block", "cursor": "pointer"}),
                            ],
                            className="d-flex align-items-center",
                        ),
                        dbc.Textarea(id="crit-json-msg", value=_AGENT_CRITERIA_MSG, style=_mono(140), className="mb-2"),
                        dbc.Label("Paste the JSON your AI returned", className="small fw-bold mb-0"),
                        dbc.Textarea(id="crit-json-input", placeholder='{ "criteria": [ ... ] }', style=_mono(110), className="mb-1"),
                        html.Div(
                            [
                                dbc.Button("Validate", id="crit-json-validate", color="secondary", outline=True, size="sm", className="me-2"),
                                dbc.Button("Load into editor", id="crit-json-load", color="primary", size="sm", disabled=True),
                            ],
                            className="mb-1",
                        ),
                        html.Div(id="crit-json-report"),
                        dcc.Store(id="crit-json-parsed"),
                    ],
                    className="ps-2",
                ),
            ],
            className="mt-2",
        ),
    ]


def _preview_column() -> list:
    return [
        html.Div(
            [
                html.H6("Preview (what the AI receives)", className="mb-0"),
                dbc.Button("Collapse", id="crit-preview-expand", color="link", size="sm", className="p-0"),
            ],
            className="d-flex justify-content-between align-items-center",
        ),
        html.P("The exact criteria text filled into both the screening and extraction prompts.", className="text-muted small mb-1"),
        html.Pre(id="crit-preview", style=_PREVIEW_EXPANDED),
    ]


def layout() -> Any:
    rows = _initial_rows()
    return html.Div(
        [
            dbc.Row([dbc.Col(_editor_column(rows), width=7), dbc.Col(_preview_column(), width=5)]),
            dbc.Modal(
                [
                    dbc.ModalHeader(dbc.ModalTitle("Edit criterion")),
                    dbc.ModalBody([dbc.Input(id="crit-edit-name", placeholder="Criterion name", size="sm", className="mb-1"), *_passfail_fields("crit-edit")]),
                    dbc.ModalFooter([dbc.Button("Cancel", id="crit-edit-cancel", color="link"), dbc.Button("Save", id="crit-edit-save", color="primary")]),
                ],
                id="crit-edit-modal",
                is_open=False,
            ),
            dcc.Store(id="crit-edit-idx"),
            dcc.Store(id="crit-store", data=rows),
        ]
    )


def register_callbacks(app: Any) -> None:
    @app.callback(
        Output("crit-store", "data"),
        Output("crit-add-name", "value"),
        Output("crit-add-pass", "value"),
        Output("crit-add-fail", "value"),
        Output("crit-add-uncertain", "value"),
        Output("crit-add-feedback", "children"),
        Input("crit-add", "n_clicks"),
        Input({"type": "crit-remove", "idx": ALL}, "n_clicks"),
        Input({"type": "crit-moveup", "idx": ALL}, "n_clicks"),
        Input({"type": "crit-movedown", "idx": ALL}, "n_clicks"),
        Input("crit-import-load", "n_clicks"),
        Input("crit-json-load", "n_clicks"),
        State("crit-add-name", "value"),
        State("crit-add-pass", "value"),
        State("crit-add-fail", "value"),
        State("crit-add-uncertain", "value"),
        State("crit-store", "data"),
        State("crit-import-text", "value"),
        State("crit-json-parsed", "data"),
        prevent_initial_call=True,
    )
    def _mutate(_add, _rm, _up, _down, _imp, _json, name, p, f, u, store, import_text, parsed):
        trig = ctx.triggered_id
        keep = (no_update,) * 5
        if trig == "crit-import-load":
            return (import_from_text(import_text or ""), *keep)
        if trig == "crit-json-load":
            return (parsed or no_update, *keep)

        rows = list(store or [])
        if trig == "crit-add":
            if not (name or "").strip() and not (p or "").strip():
                return no_update, no_update, no_update, no_update, no_update, dbc.Alert("Give the criterion a name or a PASS rule.", color="warning", className="mb-0 py-1")
            rows.append({"id": "", "name": (name or "").strip(), "pass_if": (p or "").strip(), "fail_if": (f or "").strip(), "uncertain_if": (u or "").strip()})
            return rows, "", "", "", "", dbc.Alert(f"Added '{(name or '').strip() or 'criterion'}'.", color="success", className="mb-0 py-1")
        if isinstance(trig, dict) and any(c.get("value") for c in (ctx.triggered or [])):
            idx = trig.get("idx")
            if isinstance(idx, int) and 0 <= idx < len(rows):
                if trig["type"] == "crit-remove":
                    rows.pop(idx)
                elif trig["type"] == "crit-moveup" and idx > 0:
                    rows[idx - 1], rows[idx] = rows[idx], rows[idx - 1]
                elif trig["type"] == "crit-movedown" and idx < len(rows) - 1:
                    rows[idx + 1], rows[idx] = rows[idx], rows[idx + 1]
                return (rows, *keep)
        return (no_update, *keep)

    @app.callback(
        Output("crit-list", "children"),
        Output("crit-preview", "children"),
        Input("crit-store", "data"),
    )
    def _render(rows):
        rows = rows or []
        return _render_list(rows), _render_preview(rows)

    @app.callback(
        Output("crit-edit-modal", "is_open"),
        Output("crit-edit-idx", "data"),
        Output("crit-edit-name", "value"),
        Output("crit-edit-pass", "value"),
        Output("crit-edit-fail", "value"),
        Output("crit-edit-uncertain", "value"),
        Input({"type": "crit-edit", "idx": ALL}, "n_clicks"),
        Input("crit-edit-cancel", "n_clicks"),
        State("crit-store", "data"),
        prevent_initial_call=True,
    )
    def _open_edit(_edits, _cancel, store):
        trig = ctx.triggered_id
        if trig == "crit-edit-cancel":
            return False, no_update, no_update, no_update, no_update, no_update
        if not isinstance(trig, dict) or not any(c.get("value") for c in (ctx.triggered or [])):
            return (no_update,) * 6
        idx = trig.get("idx")
        rows = store or []
        if not (isinstance(idx, int) and 0 <= idx < len(rows)):
            return (no_update,) * 6
        c = rows[idx]
        return True, {"idx": idx}, c.get("name", ""), c.get("pass_if", ""), c.get("fail_if", ""), c.get("uncertain_if", "")

    @app.callback(
        Output("crit-store", "data", allow_duplicate=True),
        Output("crit-edit-modal", "is_open", allow_duplicate=True),
        Input("crit-edit-save", "n_clicks"),
        State("crit-edit-idx", "data"),
        State("crit-edit-name", "value"),
        State("crit-edit-pass", "value"),
        State("crit-edit-fail", "value"),
        State("crit-edit-uncertain", "value"),
        State("crit-store", "data"),
        prevent_initial_call=True,
    )
    def _save_edit(n, idxdata, name, p, f, u, store):
        if not n or not idxdata:
            return no_update, no_update
        rows = list(store or [])
        idx = idxdata.get("idx")
        if not (isinstance(idx, int) and 0 <= idx < len(rows)):
            return no_update, False
        rows[idx] = {**rows[idx], "name": (name or "").strip(), "pass_if": (p or "").strip(), "fail_if": (f or "").strip(), "uncertain_if": (u or "").strip()}
        return rows, False

    @app.callback(
        Output("crit-preview", "style"),
        Output("crit-preview-expand", "children"),
        Input("crit-preview-expand", "n_clicks"),
    )
    def _toggle_preview(n):
        collapsed = bool(n) and n % 2 == 1
        return (_PREVIEW_COLLAPSED, "Expand") if collapsed else (_PREVIEW_EXPANDED, "Collapse")

    @app.callback(
        Output("crit-json-report", "children"),
        Output("crit-json-parsed", "data"),
        Output("crit-json-load", "disabled"),
        Input("crit-json-validate", "n_clicks"),
        State("crit-json-input", "value"),
        prevent_initial_call=True,
    )
    def _validate_json(n, raw):
        if not n:
            return no_update, no_update, no_update
        rows, report = parse_criteria_import(raw or "")
        if report.has_errors or not rows:
            return _report_view(report), None, True
        return _report_view(report), rows, False

    @app.callback(
        Output("crit-feedback", "children"),
        Input("crit-save", "n_clicks"),
        State("crit-store", "data"),
        prevent_initial_call=True,
    )
    def _save(n, store):
        if not n:
            return no_update
        rows = [r for r in (store or []) if r.get("name", "").strip() or r.get("pass_if", "").strip() or r.get("fail_if", "").strip()]
        if not rows:
            return dbc.Alert("Add at least one criterion before saving.", color="warning", className="mb-0 py-1")
        project = get_project()
        path = project.root / project.config.screening.criteria_structured
        try:
            cs = save_criteria(path, rows)
        except Exception as e:
            return dbc.Alert(f"Save failed: {e}", color="danger", className="mb-0 py-1")

        archive = _write_criteria_json(project.root, cs)  # re-importable JSON backup next to the project
        suffix = f" (+ {archive.name} backup)" if archive else ""
        msgs = [dbc.Alert(f"Saved {len(cs.criteria)} criteria to {path.name}{suffix}.", color="success", className="mb-0 py-1")]
        try:
            n_ext = project.db.count_sources_with_extraction(project.project_id, "ai")
        except Exception:
            n_ext = 0
        if n_ext:
            msgs.append(dbc.Alert(
                f"{n_ext} paper(s) were already AI-extracted under the previous criteria — re-run extraction "
                "(and re-calibrate) so their flag-checks reflect the updated criteria.",
                color="warning", className="mb-0 py-1 mt-1",
            ))
        return msgs

    version_ui.register(app, "crit", _KIND, _to_text, Output("crit-store", "data", allow_duplicate=True), lambda c: json.loads(c).get("criteria", []))
    version_ui.register_save(app, "crit", _KIND, "crit-save", [State("crit-store", "data")], _to_content)
