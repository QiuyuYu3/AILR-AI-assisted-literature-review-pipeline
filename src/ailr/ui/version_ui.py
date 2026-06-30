"""Version history + diff UI shared by the criteria, variables, and prompt editors."""

import difflib
from typing import Any, Callable

import dash_bootstrap_components as dbc
from dash import Input, Output, State, html, no_update

from ailr.ui._common import get_project

_LINE = {"fontFamily": "monospace", "fontSize": "0.75rem", "whiteSpace": "pre-wrap", "padding": "0 6px"}


def options(kind: str) -> list:
    p = get_project()
    return [{"label": f"{v['version']} · {v['created_at']}", "value": v["version"]}
            for v in p.db.list_artifact_versions(p.project_id, kind)]


def save_version(kind: str, content: str):
    p = get_project()
    return p.db.save_artifact_version(p.project_id, kind, content)


def diff_view(a_text: str, b_text: str) -> Any:
    a, b = a_text.splitlines(), b_text.splitlines()
    if a == b:
        return dbc.Alert("No differences between these two versions.", color="light", className="py-1 small mb-0")
    out = []
    for ln in difflib.ndiff(a, b):
        tag, txt = ln[:2], ln[2:]
        if tag == "+ ":
            out.append(html.Div(f"+ {txt}", style={**_LINE, "backgroundColor": "#e6ffed", "color": "#22863a"}))
        elif tag == "- ":
            out.append(html.Div(f"- {txt}", style={**_LINE, "backgroundColor": "#ffeef0", "color": "#b31d28"}))
        elif tag == "? ":
            continue
        else:
            out.append(html.Div(f"  {txt}", style={**_LINE, "color": "#444"}))
    return html.Div(out, style={"border": "1px solid #eee", "borderRadius": "6px", "padding": "6px", "maxHeight": "50vh", "overflow": "auto"})


def history_layout(prefix: str, kind: str, title: str = "Version history & diff") -> Any:
    opts = options(kind)
    b = opts[0]["value"] if opts else None
    a = opts[1]["value"] if len(opts) > 1 else b
    return html.Details(
        [
            html.Summary(title),
            html.Div(
                [
                    dbc.Label("Restore a saved version", className="small fw-bold mb-0 mt-2"),
                    dbc.InputGroup(
                        [
                            dbc.Select(id=f"{prefix}-ver-restore-sel", options=opts, value=b, size="sm"),
                            dbc.Button("Restore to editor", id=f"{prefix}-ver-restore", color="secondary", outline=True, size="sm"),
                        ],
                        size="sm",
                    ),
                    html.Div(id=f"{prefix}-ver-restore-fb", className="small mt-1 mb-2"),
                    dbc.Label("Compare two versions", className="small fw-bold mb-0"),
                    dbc.Row(
                        [
                            dbc.Col(dbc.Select(id=f"{prefix}-ver-a", options=opts, value=a, size="sm"), width=5),
                            dbc.Col(html.Span("→", className="text-muted"), width="auto", className="pt-1 px-0"),
                            dbc.Col(dbc.Select(id=f"{prefix}-ver-b", options=opts, value=b, size="sm"), width=5),
                        ],
                        className="g-2 align-items-center mb-2",
                    ),
                    html.Div(id=f"{prefix}-ver-diff"),
                ],
                className="ps-2",
            ),
        ],
        className="mt-3",
    )


def register(app, prefix: str, kind: str, to_text: Callable[[str], str],
             restore_output, restore_transform: Callable[[str], Any]) -> None:
    """Wire the compare/diff + restore callbacks for one editor."""
    def _vtext(version):
        if not version:
            return ""
        p = get_project()
        v = p.db.get_artifact_version(p.project_id, kind, version)
        return to_text(v["content"]) if v else ""

    @app.callback(
        Output(f"{prefix}-ver-diff", "children"),
        Input(f"{prefix}-ver-a", "value"),
        Input(f"{prefix}-ver-b", "value"),
    )
    def _diff(va, vb):
        if not va or not vb:
            return html.Small("Save a few versions, then pick two to compare.", className="text-muted")
        return diff_view(_vtext(va), _vtext(vb))

    @app.callback(
        restore_output,
        Output(f"{prefix}-ver-restore-fb", "children"),
        Input(f"{prefix}-ver-restore", "n_clicks"),
        State(f"{prefix}-ver-restore-sel", "value"),
        prevent_initial_call=True,
    )
    def _restore(n, version):
        if not n or not version:
            return no_update, no_update
        p = get_project()
        v = p.db.get_artifact_version(p.project_id, kind, version)
        if not v:
            return no_update, dbc.Alert("Version not found.", color="warning", className="mb-0 py-1")
        try:
            value = restore_transform(v["content"])
        except Exception:
            return no_update, dbc.Alert("Could not read that version.", color="danger", className="mb-0 py-1")
        return value, dbc.Alert(f"Loaded {version} — review, then Save to keep it.", color="info", className="mb-0 py-1")


def register_save(app, prefix: str, kind: str, save_btn_id: str, content_states: list, to_content: Callable[..., str]) -> None:
    """Snapshot a version (deduped) and refresh the selects when the editor's Save button is clicked."""
    @app.callback(
        Output(f"{prefix}-ver-restore-sel", "options"),
        Output(f"{prefix}-ver-a", "options"),
        Output(f"{prefix}-ver-b", "options"),
        Input(save_btn_id, "n_clicks"),
        *content_states,
        prevent_initial_call=True,
    )
    def _snapshot(n, *vals):
        if not n:
            return no_update, no_update, no_update
        try:
            content = to_content(*vals)
        except Exception:
            return no_update, no_update, no_update
        save_version(kind, content)
        opts = options(kind)
        return opts, opts, opts
