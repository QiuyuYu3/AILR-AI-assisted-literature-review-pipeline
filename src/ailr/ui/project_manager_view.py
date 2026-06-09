"""Project manager landing page: create / open a project from the browser.

Shown by app.py when no project is loaded (i.e. `ailr ui` launched without a path).
On success it sets the global project and reloads the page into the full review UI.
"""

import time
from pathlib import Path

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update

from ailr.exceptions import AILRError
from ailr.ui._common import create_project, list_recent_projects, switch_project

_MODE_OPTIONS = [
    {"label": "Assisted (AI + 1 human, blinded)", "value": "assisted"},
    {"label": "Independent / strict (2 humans, blinded)", "value": "strict"},
    {"label": "Custom", "value": "custom"},
]

_STORAGE_OPTIONS = [
    {"label": "Local file (SQLite) — default, no setup", "value": "sqlite"},
    {"label": "Shared database (PostgreSQL) — for multi-person", "value": "postgres"},
]


def _recent_block():
    recent = list_recent_projects()
    if not recent:
        return html.Div("No recent projects.", className="text-muted small")
    return html.Div(
        [
            dbc.Button(
                Path(p).name + "  —  " + p,
                id={"type": "pm-recent", "path": p},
                color="link",
                size="sm",
                className="d-block text-start p-1",
            )
            for p in recent
        ]
    )


def layout():
    return dbc.Container(
        [
            dcc.Location(id="pm-url", refresh=True),
            html.H3("ailr — Project manager", className="mt-4 mb-1"),
            html.P(
                "Create a new review project or open an existing one.",
                className="text-muted",
            ),
            dbc.Row(
                [
                    dbc.Col(
                        dbc.Card(
                            dbc.CardBody(
                                [
                                    html.H5("New project", className="mb-3"),
                                    dbc.Label("Project name", className="small fw-bold"),
                                    dbc.Input(id="pm-new-name", placeholder="e.g. dyadic-scoping-review", size="sm", className="mb-2"),
                                    dbc.Label("Parent folder (where to create it)", className="small fw-bold"),
                                    dbc.Input(
                                        id="pm-new-parent",
                                        placeholder=r"e.g. C:\research_projects\LR_pipeline",
                                        value=str(Path.cwd()),
                                        size="sm",
                                        className="mb-2",
                                    ),
                                    dbc.Label("Review mode", className="small fw-bold"),
                                    dbc.Select(id="pm-new-mode", options=_MODE_OPTIONS, value="assisted", size="sm", className="mb-2"),
                                    dbc.Label("Storage", className="small fw-bold"),
                                    dbc.RadioItems(id="pm-new-storage", options=_STORAGE_OPTIONS, value="sqlite", className="mb-2"),
                                    dbc.Collapse(
                                        [
                                            dbc.Label("PostgreSQL connection URL", className="small fw-bold"),
                                            dbc.Input(
                                                id="pm-new-pgurl",
                                                placeholder="postgresql+psycopg://user:password@host:5432/dbname",
                                                size="sm",
                                                className="mb-1",
                                            ),
                                            html.Div(
                                                "Tip: a free Neon/Supabase database gives you this URL. Requires the 'postgres' extra (pip install ailr[postgres]).",
                                                className="text-muted small mb-2",
                                            ),
                                        ],
                                        id="pm-new-pg-collapse",
                                        is_open=False,
                                    ),
                                    dbc.Button("Create project", id="pm-new-create", color="primary", size="sm"),
                                    html.Div(id="pm-new-feedback", className="small mt-2"),
                                ]
                            ),
                            className="h-100",
                        ),
                        md=6,
                    ),
                    dbc.Col(
                        dbc.Card(
                            dbc.CardBody(
                                [
                                    html.H5("Open existing", className="mb-3"),
                                    dbc.Label("Project folder (contains lit_review.yaml)", className="small fw-bold"),
                                    dbc.InputGroup(
                                        [
                                            dbc.Input(id="pm-open-path", placeholder=r"e.g. C:\research_projects\LR_pipeline\test", size="sm"),
                                            dbc.Button("Open", id="pm-open-btn", color="secondary", size="sm"),
                                        ],
                                        className="mb-2",
                                    ),
                                    html.Div(id="pm-open-feedback", className="small mb-3"),
                                    html.H6("Recent", className="mt-2"),
                                    _recent_block(),
                                ]
                            ),
                            className="h-100",
                        ),
                        md=6,
                    ),
                ]
            ),
        ],
        fluid=True,
    )


def register_callbacks(app):
    @app.callback(
        Output("pm-new-pg-collapse", "is_open"),
        Input("pm-new-storage", "value"),
    )
    def _toggle_pg(storage):
        return storage == "postgres"

    @app.callback(
        Output("pm-url", "href", allow_duplicate=True),
        Output("pm-new-feedback", "children"),
        Input("pm-new-create", "n_clicks"),
        State("pm-new-name", "value"),
        State("pm-new-parent", "value"),
        State("pm-new-mode", "value"),
        State("pm-new-storage", "value"),
        State("pm-new-pgurl", "value"),
        prevent_initial_call=True,
    )
    def _create(_n, name, parent, mode, storage, pgurl):
        if not name or not name.strip():
            return no_update, dbc.Alert("Please enter a project name.", color="warning", className="py-2 mb-0")
        if not parent or not parent.strip():
            return no_update, dbc.Alert("Please enter a parent folder.", color="warning", className="py-2 mb-0")
        db_url = pgurl if storage == "postgres" else None
        if storage == "postgres" and (not db_url or not db_url.strip()):
            return no_update, dbc.Alert("Please enter the PostgreSQL connection URL.", color="warning", className="py-2 mb-0")
        try:
            create_project(Path(parent.strip()), name.strip(), mode=mode or "assisted", database_url=db_url)
        except AILRError as e:
            return no_update, dbc.Alert(f"Could not create project: {e}", color="danger", className="py-2 mb-0")
        except Exception as e:  # surface DB-connection errors (e.g. bad PG URL, missing psycopg)
            return no_update, dbc.Alert(f"Created folder but could not open the database: {e}", color="danger", className="py-2 mb-0")
        return f"/?o={int(time.time())}", no_update

    @app.callback(
        Output("pm-url", "href", allow_duplicate=True),
        Output("pm-open-feedback", "children"),
        Input("pm-open-btn", "n_clicks"),
        Input({"type": "pm-recent", "path": ALL}, "n_clicks"),
        State("pm-open-path", "value"),
        prevent_initial_call=True,
    )
    def _open(_btn, _recent_clicks, path):
        trig = ctx.triggered_id
        target = None
        if isinstance(trig, dict) and trig.get("type") == "pm-recent":
            target = trig.get("path")
        elif trig == "pm-open-btn":
            target = (path or "").strip()
        if not target:
            return no_update, dbc.Alert("Please enter a project folder path.", color="warning", className="py-2 mb-0")
        if not (Path(target) / "lit_review.yaml").exists():
            return no_update, dbc.Alert("No lit_review.yaml in that folder.", color="danger", className="py-2 mb-0")
        try:
            switch_project(Path(target))
        except Exception as e:
            return no_update, dbc.Alert(f"Could not open project: {e}", color="danger", className="py-2 mb-0")
        return f"/?o={int(time.time())}", no_update
