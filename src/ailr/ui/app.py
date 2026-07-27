"""Dash app: review UI with a left-side nav. Launched by `ailr ui`."""

import os
import time
from pathlib import Path

import dash_bootstrap_components as dbc
from dash import ALL, Dash, Input, Output, State, ctx, dcc, html, no_update
from flask import abort, send_file

from ailr.ui import (
    calibration_view,
    conflicts_view,
    consensus_view,
    dashboard_view,
    database_view,
    duplicates_view,
    extract_view,
    ft_conflicts_view,
    full_text_view,
    import_view,
    modals,
    preprocess_view,
    project_manager_view,
    protocol_view,
    reports_view,
    screen_view,
    settings_view,
    sources_view,
    tags_view,
    template_view,
    workflow_view,
)
from ailr.ui._common import triggered_click_id, workflow_summary
from ailr.ui._project import get_project, has_project, resolve_pdf_path



def _nav_section(label: str):
    return html.Div(
        label.upper(),
        className="small fw-bold mt-3 mb-1 px-2",
        style={"letterSpacing": "0.06em", "color": "var(--ailr-text)"},
    )


def _nav_link(label: str, tab: str):
    return dbc.NavLink(
        label,
        id={"type": "nav-link", "tab": tab},
        n_clicks=0,
        active=(tab == "dashboard"),
        href="#",
    )


def _build_sidebar():
    return dbc.Nav(
        [
            _nav_link("Projects", "projects"),
            _nav_link("Protocol", "protocol"),
            _nav_link("Summary", "dashboard"),
            _nav_section("Abstract"),
            _nav_link("Workflow", "workflow_abstract"),
            _nav_link("Screening", "screen"),
            _nav_link("Conflicts", "conflicts"),
            _nav_section("Full text"),
            _nav_link("Workflow", "workflow_fulltext"),
            _nav_link("Full-text review", "full_text"),
            _nav_link("FT Conflicts", "ft_conflicts"),
            _nav_section("Manage"),
            _nav_link("Import", "import"),
            _nav_link("Sources", "sources"),
            _nav_link("Tags", "tags"),
            _nav_link("Duplicates", "duplicates"),
            _nav_link("Database", "database"),
            _nav_link("Reports", "reports"),
            _nav_link("Settings", "settings"),
        ],
        vertical=True,
        pills=True,
    )


def build_app() -> Dash:
    app = Dash(
        __name__,
        external_stylesheets=[dbc.themes.BOOTSTRAP],
        suppress_callback_exceptions=True,
        title="ailr review",
    )
    app.layout = dbc.Container(
        fluid=True,
        children=[
            dbc.Row(
                [
                    dbc.Col(
                        [
                            dbc.Button(
                                "Hide nav",
                                id="sidebar-toggle",
                                color="link",
                                size="sm",
                                className="p-0 mt-3 mb-1 d-block",
                            ),
                            html.Div(id="app-header"),
                        ],
                        width=8,
                    ),
                    dbc.Col(
                        [
                            dbc.Label("Your reviewer ID", className="small fw-bold mt-3"),
                            dbc.Input(
                                id="shared-reviewer",
                                placeholder="your name or initials",
                                persistence=True,
                                persistence_type="local",
                                size="sm",
                            ),
                        ],
                        width=4,
                    ),
                ],
                className="mb-2",
            ),
            html.Hr(className="mt-0"),
            dbc.Row(
                [
                    dbc.Col(_build_sidebar(), id="sidebar-col", width=2, className="pe-3"),
                    dbc.Col(html.Div(id="tab-content"), id="content-col", width=10, className="ps-4"),
                ],
            ),
            dcc.Location(id="settings-redirect", refresh=True),
            dcc.Store(id="tabs", data="dashboard", storage_type="session"),
            dcc.Store(id="screen-store", data={"idx": 0}),
            dcc.Store(id="extract-store", data={"sid": None}, storage_type="session"),
            dcc.Store(id="extract-refresh", data={"ts": 0}),
            dcc.Store(id="cons-store", data={"sid": None}, storage_type="session"),
            dcc.Store(id="cons-refresh", data={"ts": 0}),
            dcc.Store(id="cons-state", data={}),
            dcc.Store(id="screen-page", data={"page": 0}),
            dcc.Store(id="screen-refresh", data={"ts": 0}),
            dcc.Store(id="screen-last-action", data=None),
            dcc.Store(id="conflicts-refresh", data={"ts": 0}),
            dcc.Store(id="ft-refresh", data={"ts": 0}),
            dcc.Store(id="ft-last-action", data=None),
            dcc.Store(id="ft-page", data={"page": 0}),
            dcc.Store(id="ft-conflicts-refresh", data={"ts": 0}),
            dcc.Store(id="tags-refresh", data={"ts": 0}),
            dcc.Store(id="tags-delete-pending", data=None),
            *modals.layout(),
        ],
    )

    project_manager_view.register_callbacks(app)

    @app.callback(
        Output("app-header", "children"),
        Input("tabs", "data"),
    )
    def _app_header(_tab):
        if not has_project():
            return html.H4("ailr — no project open", className="mb-1 text-muted")
        cfg = get_project().config
        return [
            html.H4(f"ailr — {cfg.project.name}", className="mb-1"),
            html.P(workflow_summary(cfg), className="text-muted small mb-2"),
        ]

    @app.server.route("/pdf/<int:sid>")
    def _serve_pdf(sid: int):
        src = get_project().db.get_source(sid)
        if src is None:
            abort(404)
        p = resolve_pdf_path(src)
        if p is None or not p.exists():
            abort(404)
        return send_file(str(p), mimetype="application/pdf")

    @app.callback(
        Output("tab-content", "children"),
        Input("tabs", "data"),
        State("shared-reviewer", "value"),
    )
    def _render_tab(tab: str, reviewer):
        # A failure while building a tab's layout (e.g. a transient DB hiccup) would otherwise be
        # swallowed and leave the content blank ("nothing happens until I refresh"); surface it instead.
        try:
            layout = _tab_layout(tab, reviewer)
            # Key by tab so switching forces a clean remount — avoids an async dcc.Markdown / Suspense teardown race that blanks the page.
            return html.Div(layout, key=f"tab-{tab}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            return dbc.Alert(
                [
                    "Could not load this tab: ",
                    html.Code(str(e)),
                    html.Br(),
                    html.Small("Often a transient database hiccup — click the tab again. If it keeps happening, copy this message."),
                ],
                color="danger",
                className="m-3",
            )

    def _tab_layout(tab: str, reviewer):
        # Project gate: with no project open, every tab shows the project manager.
        if tab == "projects" or not has_project():
            return project_manager_view.layout()
        if tab == "protocol":
            return protocol_view.layout()
        if tab == "dashboard":
            return dashboard_view.layout(reviewer or "")
        if tab == "extract":
            return extract_view.layout()
        if tab == "consensus":
            return consensus_view.layout()
        if tab == "sources":
            return sources_view.layout()
        if tab == "conflicts":
            return conflicts_view.layout()
        if tab == "tags":
            return tags_view.layout()
        if tab in ("full_text", "workflow_fulltext", "ft_conflicts"):
            try:
                from ailr.ingest.pdf_link import auto_link_pdfs

                auto_link_pdfs(get_project())
            except Exception:
                pass
        if tab == "full_text":
            return full_text_view.layout()
        if tab == "ft_conflicts":
            return ft_conflicts_view.layout()
        if tab == "duplicates":
            return duplicates_view.layout()
        if tab == "database":
            return database_view.layout()
        if tab == "reports":
            return reports_view.layout()
        if tab == "import":
            return import_view.layout()
        if tab == "settings":
            return settings_view.layout()
        if tab == "workflow_abstract":
            return workflow_view.layout("abstract")
        if tab == "workflow_fulltext":
            return workflow_view.layout("full_text")
        return screen_view.layout()

    @app.callback(
        Output("tabs", "data"),
        Input({"type": "nav-link", "tab": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def _switch_tab(_clicks):
        triggered = triggered_click_id()
        if triggered is None:
            return no_update
        return triggered.get("tab", no_update)

    @app.callback(
        Output({"type": "nav-link", "tab": ALL}, "active"),
        Input("tabs", "data"),
        State({"type": "nav-link", "tab": ALL}, "id"),
    )
    def _set_active(current_tab, ids):
        return [link_id.get("tab") == current_tab for link_id in (ids or [])]

    @app.callback(
        Output("sidebar-col", "width"),
        Output("sidebar-col", "style"),
        Output("content-col", "width"),
        Output("sidebar-toggle", "children"),
        Input("sidebar-toggle", "n_clicks"),
        State("sidebar-col", "width"),
        prevent_initial_call=True,
    )
    def _toggle_sidebar(_n, current_width):
        if current_width == 2:
            return 0, {"display": "none"}, 12, "Show nav"
        return 2, {}, 10, "Hide nav"

    screen_view.register_callbacks(app)
    calibration_view.register_callbacks(app, "abstract")
    calibration_view.register_callbacks(app, "extraction")
    extract_view.register_callbacks(app)
    consensus_view.register_callbacks(app)
    sources_view.register_callbacks(app)
    conflicts_view.register_callbacks(app)
    dashboard_view.register_callbacks(app)
    tags_view.register_callbacks(app)
    full_text_view.register_callbacks(app)
    preprocess_view.register_callbacks(app)
    ft_conflicts_view.register_callbacks(app)
    reports_view.register_callbacks(app)
    import_view.register_callbacks(app)
    duplicates_view.register_callbacks(app)
    database_view.register_callbacks(app)
    template_view.register_callbacks(app)
    protocol_view.register_callbacks(app)
    settings_view.register_callbacks(app)
    workflow_view.register_callbacks(app)


    modals.register_callbacks(app)

    return app


def main() -> None:
    app = build_app()
    port = int(os.environ.get("AILR_UI_PORT", "8050"))
    app.run(port=port, debug=False)


if __name__ == "__main__":
    main()
