"""Settings tab: configure the LLM (provider/model/temperature) and view the AI prompts."""

import os
from pathlib import Path
from typing import Any

import dash_bootstrap_components as dbc
from dash import Input, Output, State, html, no_update

from ailr.core.config import resolve_stage_llm, save_project_type, save_stage_llm_config
from ailr.extraction import (
    compose_extraction_prompt,
    compose_schema,
    compose_screening_prompt,
    schema_to_markdown,
)
from ailr.ui._common import help_icon, prompt_view_toggle, render_prompt_body

from ailr.ui._project import clear_current_project_data, delete_current_project, get_project, read_criteria, read_screening_additional, reload_project

_PROVIDERS = [
    {"label": "Anthropic", "value": "anthropic"},
    {"label": "OpenAI", "value": "openai"},
    {"label": "Gemini", "value": "gemini"},
]

_API_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}

# The preset a project was created with only seeds its defaults; the workflows it set are edited
# on each stage's Workflow page, so this is shown as a fact about the project, not a control.
_MODE_BLURB = {
    "strict": "two independent human reviewers at screening and extraction, reconciled.",
    "assisted": "AI plus one human at screening, AI-extract then human-verify.",
    "custom": "defaults from your own preset file.",
}

_REVIEW_TYPE_OPTIONS = [
    {"label": "Scoping review", "value": "scoping"},
    {"label": "Systematic review", "value": "systematic"},
]


def _read_file(project_root: Path, rel: str) -> str:
    p = project_root / rel
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return f"(not found: {rel})"


def _resolved_screening_prompt(project: Any) -> str:
    """The exact screening system prompt sent to the AI — template with criteria + additional filled in."""
    template = _read_file(project.root, project.config.screening.prompt)
    return compose_screening_prompt(template, criteria=read_criteria(""), additional=read_screening_additional())


def _resolved_extraction_prompt(project: Any) -> str:
    """The exact extraction system prompt — template with criteria, schema, and additional filled in."""
    template = _read_file(project.root, project.config.extraction.prompt)
    additional = _read_file(project.root, project.config.extraction.additional)
    if additional.startswith("(not found:"):
        additional = ""
    try:
        schema_md = schema_to_markdown(compose_schema(project.root / project.config.extraction.schema_path))
    except Exception:
        schema_md = "(schema not set — see the Template tab)"
    return compose_extraction_prompt(template, criteria=read_criteria(""), schema_md=schema_md, additional=additional)


def layout() -> Any:
    project = get_project()
    llm = project.config.llm
    sc = project.config.screening.llm
    ex = project.config.extraction.llm
    sc_eff = resolve_stage_llm(llm, sc)
    ex_eff = resolve_stage_llm(llm, ex)
    providers_used = sorted({sc_eff.provider, ex_eff.provider})
    key_badges = [(p, _API_KEY_ENV.get(p, "ANTHROPIC_API_KEY")) for p in providers_used]
    db_url_set = bool(project.config.storage.database_url)

    project_block = [
        dbc.Label("Review type", className="small fw-bold"),
        dbc.Select(
            id="settings-project-type",
            options=_REVIEW_TYPE_OPTIONS,
            value=project.config.project.type,
            size="sm",
            style={"maxWidth": "320px"},
        ),
        html.Small(
            "How the review is described in the PRISMA report and the methods skeleton.",
            className="text-muted d-block mb-1",
        ),
        html.Div(id="settings-project-type-saved", className="text-success small mb-2"),
        html.Div(
            [
                html.Span("Created with the ", className="text-muted"),
                html.Strong(project.config.project.mode),
                html.Span(" preset: ", className="text-muted"),
                html.Span(_MODE_BLURB.get(project.config.project.mode, ""), className="text-muted"),
            ],
            className="small mb-3",
        ),
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
                    "Shared Postgres" if db_url_set else "Local SQLite",
                    color="success" if db_url_set else "secondary",
                    className="me-2",
                ),
                html.Small(
                    "The database comes from storage.database_url in this project's lit_review.yaml. "
                    "Set it to a PostgreSQL URL to share the data with a team; leave it blank to use the "
                    "local SQLite file above. The yaml holds the DB password, so do not commit it to a "
                    "public git repo (the project template gitignores lit_review.yaml).",
                    className="text-muted",
                ),
            ],
            className="mt-1",
        ),
        html.Div(
            [
                html.H6("How to use a shared database (Postgres / Neon)", className="mt-3"),
                html.P(
                    "Create a free Neon database, copy its connection URL, and add it to this project's "
                    "lit_review.yaml as-is (postgresql:// or postgres:// — the driver is set automatically):",
                    className="small text-muted mb-1",
                ),
                html.Pre(
                    'storage:\n  database_url: "postgresql://user:pw@host/db?sslmode=require"',
                    style={"whiteSpace": "pre-wrap", "fontSize": "0.8rem"},
                    className="mb-1",
                ),
                html.Small(
                    "Everyone who opens this project folder (e.g. shared via Box) connects to the same "
                    "database automatically. Each project's yaml can point to its own database. "
                    "Keep lit_review.yaml out of public git — it contains the DB password.",
                    className="text-muted",
                ),
            ],
            className="mt-1",
        ),
        html.Small("Create, open, or switch projects on the Projects page (in the sidebar).", className="text-muted d-block mt-3"),
    ]

    models_block = [
        html.P("Each stage has its own provider / model / temperature (used when Mock is off).", className="text-muted small"),
        html.Div(
            [
                dbc.Badge(
                    f"{env}: {'set' if os.environ.get(env) else 'NOT set'}",
                    color="success" if os.environ.get(env) else "secondary",
                    className="me-2",
                )
                for _p, env in key_badges
            ]
            + [html.Small("Real API runs need the provider's key in your environment; otherwise use Mock.", className="text-muted")],
            className="mt-1 mb-1",
        ),
        html.Details(
            [
                html.Summary("How to set an API key", className="small"),
                html.P("Export it in the terminal first, then launch the app from that same terminal:", className="small text-muted mb-1"),
                html.Pre(
                    "\n".join(f'export {env}="sk-..."' for _p, env in key_badges) + "\nailr ui <project-folder>",
                    style={"whiteSpace": "pre-wrap", "fontSize": "0.8rem"},
                    className="mb-1",
                ),
                html.Small(
                    "The key lives only in that shell session — nothing is saved to the project. "
                    "Add the export line to your ~/.bashrc to avoid re-typing it each time.",
                    className="text-muted",
                ),
            ],
            open=any(not os.environ.get(env) for _p, env in key_badges),
            className="mt-1",
        ),
        html.Small(
            "Screening sees every record, so a cheap model there saves the most; extraction reads "
            "whole papers and is worth a strong one.",
            className="text-muted d-block mt-3 mb-2",
        ),
        dbc.Row(
            [
                dbc.Col([dbc.Label("Abstract screening — provider", className="small"),
                         dbc.Select(id="settings-screen-provider", options=_PROVIDERS, value=sc_eff.provider)], width=3),
                dbc.Col([dbc.Label("Model", className="small"),
                         dbc.Input(id="settings-screen-model", value=sc_eff.model or "", placeholder="not set")], width=5),
                dbc.Col([dbc.Label("Temperature", className="small"),
                         dbc.Input(id="settings-screen-temp", type="number", min=0, max=2, step=0.1, value=sc_eff.temperature)], width=2),
            ],
            className="g-2",
        ),
        dbc.Row(
            [
                dbc.Col([dbc.Label("Full-text extraction — provider", className="small"),
                         dbc.Select(id="settings-extract-provider", options=_PROVIDERS, value=ex_eff.provider)], width=3),
                dbc.Col([dbc.Label("Model", className="small"),
                         dbc.Input(id="settings-extract-model", value=ex_eff.model or "", placeholder="not set")], width=5),
                dbc.Col([dbc.Label("Temperature", className="small"),
                         dbc.Input(id="settings-extract-temp", type="number", min=0, max=2, step=0.1, value=ex_eff.temperature)], width=2),
            ],
            className="g-2 mt-1",
        ),
        html.Div(dbc.Button("Save models", id="settings-stage-save", color="primary", size="sm", className="mt-2")),
        html.Div(id="settings-stage-feedback", className="small mt-2"),
    ]

    prompts_block = [
        html.P(
            [
                "What the AI is actually given (view-only) — the exact text sent, with criteria, schema, and "
                "additional instructions already filled in. Criteria and variables are edited on the Protocol "
                "page; each stage's prompt on its Workflow page. ",
                help_icon(
                    "These are read-only resolved previews. To change them, edit the criteria/variables on Protocol or the prompts on the Workflow pages.",
                    "settings-prompts-help",
                ),
            ],
            className="text-muted small",
        ),
        html.Details(
            [
                html.Summary("Screening prompt (as sent)"),
                prompt_view_toggle("settings-screen-prompt-render"),
                html.Div(id="settings-screen-prompt-view", children=render_prompt_body(_resolved_screening_prompt(project), "plain")),
            ]
        ),
        html.Details(
            [
                html.Summary("Extraction prompt (as sent)"),
                prompt_view_toggle("settings-extract-prompt-render"),
                html.Div(id="settings-extract-prompt-view", children=render_prompt_body(_resolved_extraction_prompt(project), "plain")),
            ]
        ),
    ]

    danger_block = [
        html.P(
            "Delete all data of this project (references, decisions, extractions, tags, duplicates…) "
            "while keeping the project and its config/prompt files — useful to redo an import from scratch. "
            "On a shared database this affects everyone. This cannot be undone.",
            className="text-muted small mb-1",
        ),
        dbc.InputGroup(
            [
                dbc.Input(id="settings-clear-confirm", placeholder=f"type '{project.root.name}' to confirm", size="sm"),
                dbc.Button("Clear all data", id="settings-clear-btn", color="danger", outline=True, size="sm"),
            ],
            style={"maxWidth": "480px"},
        ),
        html.Div(id="settings-clear-feedback", className="small mt-2"),
        html.P(
            "Delete this entire project — its data and the project row — and return to the Project "
            "manager. Files on disk (lit_review.yaml, prompts, PDFs) are kept, so you can re-open the "
            "folder later as a fresh empty project. On a shared database this affects everyone.",
            className="text-muted small mb-1 mt-3",
        ),
        dbc.InputGroup(
            [
                dbc.Input(id="settings-delproj-confirm", placeholder=f"type '{project.root.name}' to confirm", size="sm"),
                dbc.Button("Delete this project", id="settings-delproj-btn", color="danger", size="sm"),
            ],
            style={"maxWidth": "480px"},
        ),
        html.Div(id="settings-delproj-feedback", className="small mt-2"),
    ]

    tabs = dbc.Tabs(
        [
            dbc.Tab(html.Div(project_block, className="mt-3"), label="Project", tab_id="settings-tab-project"),
            dbc.Tab(html.Div(models_block, className="mt-3"), label="Models", tab_id="settings-tab-models"),
            dbc.Tab(html.Div(prompts_block, className="mt-3"), label="Prompts", tab_id="settings-tab-prompts"),
            dbc.Tab(html.Div(danger_block, className="mt-3"), label="Danger zone", tab_id="settings-tab-danger"),
        ],
        active_tab="settings-tab-project",
        className="mt-2",
    )
    return html.Div([html.H4("Settings"), tabs])


def register_callbacks(app: Any) -> None:
    @app.callback(
        Output("settings-project-type-saved", "children"),
        Input("settings-project-type", "value"),
        prevent_initial_call=True,
    )
    def _save_project_type(value):
        if not value:
            return no_update
        project = get_project()
        if value == project.config.project.type:
            return no_update
        save_project_type(project.root, value)
        reload_project()
        return f"Saved — this review is now reported as a {value.replace('_', ' ')} review."

    @app.callback(
        Output("settings-screen-prompt-view", "children"),
        Input("settings-screen-prompt-render", "value"),
    )
    def _render_settings_screen_prompt(mode):
        return render_prompt_body(_resolved_screening_prompt(get_project()), mode or "plain")

    @app.callback(
        Output("settings-extract-prompt-view", "children"),
        Input("settings-extract-prompt-render", "value"),
    )
    def _render_settings_extract_prompt(mode):
        return render_prompt_body(_resolved_extraction_prompt(get_project()), mode or "plain")

    @app.callback(
        Output("settings-stage-feedback", "children"),
        Input("settings-stage-save", "n_clicks"),
        State("settings-screen-provider", "value"),
        State("settings-screen-model", "value"),
        State("settings-screen-temp", "value"),
        State("settings-extract-provider", "value"),
        State("settings-extract-model", "value"),
        State("settings-extract-temp", "value"),
        prevent_initial_call=True,
    )
    def _save_stage_models(n, sc_prov, sc_model, sc_temp, ex_prov, ex_model, ex_temp):
        if not n:
            return no_update
        if not str(sc_model or "").strip() or not str(ex_model or "").strip():
            return dbc.Alert("Model name required for both stages.", color="warning", className="mb-0 py-1")
        project = get_project()
        try:
            save_stage_llm_config(project.root, "screening", sc_prov or None, sc_model, sc_temp)
            save_stage_llm_config(project.root, "extraction", ex_prov or None, ex_model, ex_temp)
            reload_project()
        except Exception as e:
            return dbc.Alert(f"Save failed: {e}", color="danger", className="mb-0 py-1")
        return dbc.Alert("Saved models for both stages.", color="success", className="mb-0 py-1")

    @app.callback(
        Output("settings-clear-feedback", "children"),
        Input("settings-clear-btn", "n_clicks"),
        State("settings-clear-confirm", "value"),
        prevent_initial_call=True,
    )
    def _clear_data(n, typed):
        if not n:
            return no_update
        project = get_project()
        if (typed or "").strip() != project.root.name:
            return dbc.Alert(f"Type '{project.root.name}' to confirm.", color="warning", className="mb-0 py-1")
        try:
            counts = clear_current_project_data()
        except Exception as e:
            return dbc.Alert(f"Clear failed: {e}", color="danger", className="mb-0 py-1")
        removed = sum(counts.values())
        return dbc.Alert(f"Cleared all data ({removed} row(s) removed). Re-import to start fresh.", color="success", className="mb-0 py-1")

    @app.callback(
        Output("settings-redirect", "href"),
        Output("settings-delproj-feedback", "children"),
        Input("settings-delproj-btn", "n_clicks"),
        State("settings-delproj-confirm", "value"),
        prevent_initial_call=True,
    )
    def _delete_project(n, typed):
        if not n:
            return no_update, no_update
        project = get_project()
        if (typed or "").strip() != project.root.name:
            return no_update, dbc.Alert(f"Type '{project.root.name}' to confirm.", color="warning", className="mb-0 py-1")
        try:
            delete_current_project()
        except Exception as e:
            return no_update, dbc.Alert(f"Delete failed: {e}", color="danger", className="mb-0 py-1")
        return f"/?deleted={n}", no_update
