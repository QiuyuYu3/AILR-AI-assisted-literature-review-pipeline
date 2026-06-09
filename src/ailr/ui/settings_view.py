"""Settings tab: configure the LLM (provider/model/temperature) and view the AI prompts."""

import os
from pathlib import Path
from typing import Any

import dash_bootstrap_components as dbc
from dash import Input, Output, State, html, no_update

from ailr.core.config import resolve_stage_llm, save_llm_config, save_stage_llm_config
from ailr.ui._common import (
    create_project,
    get_pdf_root,
    get_project,
    list_recent_projects,
    reload_project,
    set_pdf_root,
    switch_project,
)
from ailr.ui.screen_view import criteria_editor_block, register_criteria_callbacks

_PROVIDERS = [
    {"label": "Anthropic", "value": "anthropic"},
    {"label": "OpenAI", "value": "openai"},
    {"label": "Gemini", "value": "gemini"},
]

_STAGE_PROVIDERS = [{"label": "(inherit)", "value": ""}] + _PROVIDERS

_COMMON_MODELS = "Common: claude-opus-4-8 · claude-sonnet-4-6 · claude-haiku-4-5-20251001"

_API_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}


def _recent_options() -> list:
    opts = [{"label": "(select a recent project)", "value": ""}]
    for p in list_recent_projects():
        opts.append({"label": f"{Path(p).name}  —  {p}", "value": p})
    return opts


def _read_file(project_root: Path, rel: str) -> str:
    p = project_root / rel
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return f"(not found: {rel})"


def layout() -> Any:
    project = get_project()
    llm = project.config.llm
    sc = project.config.screening.llm
    ex = project.config.extraction.llm
    key_env = _API_KEY_ENV.get(llm.provider, "ANTHROPIC_API_KEY")
    key_set = bool(os.environ.get(key_env))
    db_url_set = bool(os.environ.get("AILR_DATABASE_URL"))

    return html.Div(
        [
            html.H4("Settings"),
            html.H6("Project", className="mt-2"),
            html.Div(
                [
                    html.Div([html.Span("Project folder: ", className="text-muted"), html.Code(str(project.root))]),
                    html.Div([html.Span("Database: ", className="text-muted"), html.Code(project.db.location_label)]),
                ],
                className="small mb-2",
            ),
            html.Div(
                [
                    dbc.Badge(
                        f"AILR_DATABASE_URL: {'set' if db_url_set else 'NOT set'}",
                        color="success" if db_url_set else "secondary",
                        className="me-2",
                    ),
                    html.Small(
                        "Set this to a PostgreSQL URL to share the project's data with a team. Unset = the local "
                        "SQLite file above. Like the API key, it lives in the environment (not in lit_review.yaml) "
                        "so the DB password is never written to / shared with the project files.",
                        className="text-muted",
                    ),
                ],
                className="mt-1",
            ),
            html.Details(
                [
                    html.Summary("How to use a shared database (Postgres / Neon)", className="small"),
                    html.P(
                        "Create a free Neon database, copy its connection URL, change the prefix to "
                        "postgresql+psycopg://, then export it and launch the app from that same terminal:",
                        className="small text-muted mb-1",
                    ),
                    html.Pre(
                        'export AILR_DATABASE_URL="postgresql+psycopg://user:pw@host/db?sslmode=require"\nailr ui <project-folder>',
                        style={"whiteSpace": "pre-wrap", "fontSize": "0.8rem"},
                        className="mb-1",
                    ),
                    html.Small(
                        "Everyone on the team exports the SAME URL → you all share one database. "
                        "Add the export line to ~/.bashrc to avoid re-typing it each session.",
                        className="text-muted",
                    ),
                ],
                open=False,
                className="mt-1",
            ),
            dbc.Label("Recent projects", className="small fw-bold mt-3"),
            dbc.InputGroup(
                [
                    dbc.Select(id="settings-recent-select", options=_recent_options(), value=""),
                    dbc.Button("Open", id="settings-recent-open", color="secondary", outline=True, size="sm"),
                ]
            ),
            dbc.Label("…or open by path", className="small fw-bold mt-3"),
            dbc.InputGroup(
                [
                    dbc.Input(id="settings-switch-path", placeholder="Path to another ailr project folder", size="sm"),
                    dbc.Button("Open project", id="settings-switch-open", color="secondary", outline=True, size="sm"),
                ]
            ),
            html.Div(id="settings-switch-feedback", className="small mt-1"),

            dbc.Label("Create a new project", className="small fw-bold mt-3"),
            dbc.Row(
                [
                    dbc.Col([dbc.Label("Name", className="small"),
                             dbc.Input(id="settings-new-name", placeholder="my-review", size="sm")], width=5),
                    dbc.Col([dbc.Label("Parent folder", className="small"),
                             dbc.Input(id="settings-new-parent", value=str(project.root.parent), size="sm")], width=7),
                ],
                className="g-2",
            ),
            html.Small("Starts in assisted mode (AI + 1 human). Change the screening workflow on the Workflow page if you want 2 independent humans.", className="text-muted"),
            html.Div(dbc.Button("Create + open", id="settings-new-create", color="primary", outline=True, size="sm", className="mt-2")),
            html.Div(id="settings-new-feedback", className="small mt-1"),

            dbc.Label("PDF folder on THIS machine", className="small fw-bold mt-3"),
            html.Small(
                "For shared drives (Box/OneDrive) where each person's path differs. If a PDF's stored path "
                "doesn't resolve, PDFs are looked up by filename under this folder. Stored locally — not shared with the team.",
                className="text-muted d-block mb-1",
            ),
            dbc.InputGroup(
                [
                    dbc.Input(id="settings-pdfroot", value=str(get_pdf_root() or ""), placeholder="e.g. C:/Users/you/Box/MyReview/pdfs", size="sm"),
                    dbc.Button("Save", id="settings-pdfroot-save", color="secondary", outline=True, size="sm"),
                ]
            ),
            html.Div(id="settings-pdfroot-feedback", className="small mt-1"),
            html.Hr(className="my-3"),
            html.H6("Model", className="mt-2"),
            html.P("Used when you run AI screening / extraction with Mock off.", className="text-muted small"),
            dbc.Row(
                [
                    dbc.Col([dbc.Label("Provider"), dbc.Select(id="settings-provider", options=_PROVIDERS, value=llm.provider)], width=3),
                    dbc.Col([dbc.Label("Model"), dbc.Input(id="settings-model", value=llm.model)], width=5),
                    dbc.Col([dbc.Label("Temperature"), dbc.Input(id="settings-temperature", type="number", min=0, max=2, step=0.1, value=llm.temperature)], width=2),
                ],
                className="g-2",
            ),
            html.Small(_COMMON_MODELS, className="text-muted"),
            html.Div(
                [
                    dbc.Badge(
                        f"{key_env}: {'set' if key_set else 'NOT set'}",
                        color="success" if key_set else "secondary",
                        className="me-2",
                    ),
                    html.Small("Real API runs need this env var; otherwise use the Mock switch.", className="text-muted"),
                ],
                className="mt-2",
            ),
            html.Details(
                [
                    html.Summary("How to set the API key", className="small"),
                    html.P(
                        "Export it in the terminal first, then launch the app from that same terminal so it inherits the key:",
                        className="small text-muted mb-1",
                    ),
                    html.Pre(
                        f'export {key_env}="sk-..."\nailr ui <project-folder>',
                        style={"whiteSpace": "pre-wrap", "fontSize": "0.8rem"},
                        className="mb-1",
                    ),
                    html.Small(
                        "The key lives only in that shell session — nothing is saved to the project. "
                        "Add the export line to your ~/.bashrc to avoid re-typing it each time.",
                        className="text-muted",
                    ),
                ],
                open=not key_set,
                className="mt-1",
            ),
            html.Div(dbc.Button("Save model settings", id="settings-save", color="primary", size="sm", className="mt-2")),
            html.Div(id="settings-save-feedback", className="small mt-2"),

            html.Hr(className="my-3"),
            html.H6("Per-stage model override", className="mt-2"),
            html.P("Optional: cheaper model for abstract screening, stronger for full-text extraction. Leave a model blank to inherit the model above.", className="text-muted small"),
            dbc.Row(
                [
                    dbc.Col([dbc.Label("Abstract screening — provider", className="small"),
                             dbc.Select(id="settings-screen-provider", options=_STAGE_PROVIDERS, value=(sc.provider or "") if sc else "")], width=3),
                    dbc.Col([dbc.Label("Model", className="small"),
                             dbc.Input(id="settings-screen-model", placeholder="(inherit)", value=(sc.model or "") if sc else "")], width=5),
                    dbc.Col(html.Small(f"effective: {resolve_stage_llm(llm, sc).model}", className="text-muted"), width=4, className="align-self-end"),
                ],
                className="g-2",
            ),
            dbc.Row(
                [
                    dbc.Col([dbc.Label("Full-text extraction — provider", className="small"),
                             dbc.Select(id="settings-extract-provider", options=_STAGE_PROVIDERS, value=(ex.provider or "") if ex else "")], width=3),
                    dbc.Col([dbc.Label("Model", className="small"),
                             dbc.Input(id="settings-extract-model", placeholder="(inherit)", value=(ex.model or "") if ex else "")], width=5),
                    dbc.Col(html.Small(f"effective: {resolve_stage_llm(llm, ex).model}", className="text-muted"), width=4, className="align-self-end"),
                ],
                className="g-2 mt-1",
            ),
            html.Small(_COMMON_MODELS, className="text-muted"),
            html.Div(dbc.Button("Save per-stage models", id="settings-stage-save", color="primary", size="sm", className="mt-2")),
            html.Div(id="settings-stage-feedback", className="small mt-2"),

            html.Hr(className="my-4"),
            html.H6("Prompts & criteria"),
            html.P("What the AI is actually given. The inclusion/exclusion criteria (used by both screening and extraction) is edited here; prompts are view-only — edit them on the Workflow pages or in the files.", className="text-muted small"),
            criteria_editor_block("settings", note="Used by both screening and data extraction (fills {{criteria}})."),
            html.Hr(className="my-3"),
            html.Details(
                [
                    html.Summary("Screening prompt"),
                    html.Pre(_read_file(project.root, project.config.screening.prompt), style={"whiteSpace": "pre-wrap", "fontSize": "0.8rem"}),
                ]
            ),
            html.Details(
                [
                    html.Summary("Extraction prompt"),
                    html.Pre(_read_file(project.root, project.config.extraction.prompt), style={"whiteSpace": "pre-wrap", "fontSize": "0.8rem"}),
                ]
            ),
            html.Small("The extraction field list (schema) is set in the Template tab.", className="text-muted"),
        ]
    )


def register_callbacks(app: Any) -> None:
    register_criteria_callbacks(app, "settings")

    @app.callback(
        Output("settings-switch-feedback", "children"),
        Output("tabs", "data", allow_duplicate=True),
        Input("settings-switch-open", "n_clicks"),
        State("settings-switch-path", "value"),
        prevent_initial_call=True,
    )
    def _switch(n, path):
        if not n:
            return no_update, no_update
        p = Path((path or "").strip())
        if not path or not (p / "lit_review.yaml").exists():
            return dbc.Alert("Not an ailr project folder (no lit_review.yaml).", color="warning", className="mb-0 py-1"), no_update
        try:
            proj = switch_project(p)
        except Exception as e:
            return dbc.Alert(f"Failed: {e}", color="danger", className="mb-0 py-1"), no_update
        return dbc.Alert(f"Opened '{proj.config.project.name}'. The app now uses this project.", color="success", className="mb-0 py-1"), "dashboard"

    @app.callback(
        Output("settings-switch-feedback", "children", allow_duplicate=True),
        Output("tabs", "data", allow_duplicate=True),
        Input("settings-recent-open", "n_clicks"),
        State("settings-recent-select", "value"),
        prevent_initial_call=True,
    )
    def _open_recent(n, path):
        if not n or not path:
            return no_update, no_update
        try:
            proj = switch_project(Path(path))
        except Exception as e:
            return dbc.Alert(f"Failed: {e}", color="danger", className="mb-0 py-1"), no_update
        return dbc.Alert(f"Opened '{proj.config.project.name}'.", color="success", className="mb-0 py-1"), "dashboard"

    @app.callback(
        Output("settings-new-feedback", "children"),
        Output("tabs", "data", allow_duplicate=True),
        Input("settings-new-create", "n_clicks"),
        State("settings-new-name", "value"),
        State("settings-new-parent", "value"),
        prevent_initial_call=True,
    )
    def _create_new(n, name, parent):
        if not n:
            return no_update, no_update
        name = (name or "").strip()
        parent = (parent or "").strip()
        if not name or not parent:
            return dbc.Alert("Enter a project name and parent folder.", color="warning", className="mb-0 py-1"), no_update
        try:
            proj = create_project(Path(parent), name, "assisted")
        except Exception as e:
            return dbc.Alert(f"Create failed: {e}", color="danger", className="mb-0 py-1"), no_update
        return dbc.Alert(f"Created + opened '{proj.config.project.name}' at {proj.root}.", color="success", className="mb-0 py-1"), "dashboard"

    @app.callback(
        Output("settings-pdfroot-feedback", "children"),
        Input("settings-pdfroot-save", "n_clicks"),
        State("settings-pdfroot", "value"),
        prevent_initial_call=True,
    )
    def _save_pdfroot(n, path):
        if not n:
            return no_update
        path = (path or "").strip()
        if path and not Path(path).is_dir():
            return dbc.Alert("That folder doesn't exist on this machine.", color="warning", className="mb-0 py-1")
        set_pdf_root(path or None)
        msg = f"PDFs on this machine resolve under {path}." if path else "Cleared — PDFs use their stored paths."
        return dbc.Alert(msg, color="success", className="mb-0 py-1")

    @app.callback(
        Output("settings-save-feedback", "children"),
        Input("settings-save", "n_clicks"),
        State("settings-provider", "value"),
        State("settings-model", "value"),
        State("settings-temperature", "value"),
        prevent_initial_call=True,
    )
    def _save(n, provider, model, temperature):
        if not n:
            return no_update
        if not model or not str(model).strip():
            return dbc.Alert("Model name required.", color="warning", className="mb-0 py-1")
        project = get_project()
        try:
            save_llm_config(project.root, provider, str(model).strip(), float(temperature if temperature is not None else 0.0))
            reload_project()
        except Exception as e:
            return dbc.Alert(f"Save failed: {e}", color="danger", className="mb-0 py-1")
        return dbc.Alert(f"Saved: {provider} / {model}.", color="success", className="mb-0 py-1")

    @app.callback(
        Output("settings-stage-feedback", "children"),
        Input("settings-stage-save", "n_clicks"),
        State("settings-screen-provider", "value"),
        State("settings-screen-model", "value"),
        State("settings-extract-provider", "value"),
        State("settings-extract-model", "value"),
        prevent_initial_call=True,
    )
    def _save_stage_models(n, sc_prov, sc_model, ex_prov, ex_model):
        if not n:
            return no_update
        project = get_project()
        try:
            save_stage_llm_config(project.root, "screening", sc_prov or None, sc_model)
            save_stage_llm_config(project.root, "extraction", ex_prov or None, ex_model)
            reload_project()
        except Exception as e:
            return dbc.Alert(f"Save failed: {e}", color="danger", className="mb-0 py-1")
        return dbc.Alert("Saved per-stage models. Blank = inherits the model above.", color="success", className="mb-0 py-1")
