"""Dash app: review UI with a left-side nav. Launched by `ailr ui`."""

import os
from pathlib import Path

import dash_bootstrap_components as dbc
from dash import ALL, Dash, Input, Output, State, ctx, dcc, html, no_update
from flask import abort, send_file

from ailr.exceptions import DuplicateError
from ailr.ui import (
    calibration_view,
    conflicts_view,
    dashboard_view,
    database_view,
    duplicates_view,
    extract_view,
    ft_conflicts_view,
    full_text_view,
    import_view,
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
from ailr.ui._common import get_project, has_project, resolve_pdf_path, triggered_click_id
from ailr.ui.screen_view import _history_block
from ailr.ui.tags_view import TAG_COLOR_OPTIONS


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
            dbc.Modal(
                [
                    dbc.ModalHeader(dbc.ModalTitle(id="history-modal-title")),
                    dbc.ModalBody(id="history-modal-body"),
                ],
                id="history-modal",
                is_open=False,
                size="lg",
                scrollable=True,
            ),
            dbc.Modal(
                [
                    dbc.ModalHeader(dbc.ModalTitle(id="tag-modal-title")),
                    dbc.ModalBody(
                        [
                            html.P(
                                "Check tags to apply to this source. Uncheck to remove. Changes save immediately.",
                                className="text-muted small",
                            ),
                            dbc.Checklist(id="tag-modal-checklist", options=[], value=[]),
                            html.Div(id="tag-modal-empty", className="text-muted small mt-2"),
                            html.Hr(),
                            html.Small("Or create a new tag:", className="fw-bold"),
                            dbc.InputGroup(
                                [
                                    dbc.Input(
                                        id="tag-modal-new-name",
                                        placeholder="New tag name",
                                        size="sm",
                                    ),
                                    dbc.Select(
                                        id="tag-modal-new-color",
                                        options=TAG_COLOR_OPTIONS,
                                        value="secondary",
                                        size="sm",
                                        style={"maxWidth": "120px"},
                                    ),
                                    dbc.Button(
                                        "Create + apply",
                                        id="tag-modal-create-apply",
                                        size="sm",
                                        color="primary",
                                    ),
                                ],
                                className="mt-1",
                            ),
                            html.Div(id="tag-modal-create-feedback", className="mt-2 small"),
                        ]
                    ),
                    dbc.ModalFooter(dbc.Button("Done", id="tag-modal-done", color="primary")),
                ],
                id="tag-modal",
                is_open=False,
                size="md",
            ),
            dcc.Store(id="tag-modal-source", data=None),
            dbc.Modal(
                [
                    dbc.ModalHeader(
                        [
                            dbc.ModalTitle(id="reader-modal-title"),
                            dbc.RadioItems(
                                id="reader-mode",
                                options=[
                                    {"label": "PDF", "value": "pdf"},
                                    {"label": "Markdown", "value": "md"},
                                ],
                                value="pdf",
                                inline=True,
                                className="ms-3",
                            ),
                        ]
                    ),
                    dbc.ModalBody(id="reader-modal-body"),
                ],
                id="reader-modal",
                is_open=False,
                size="xl",
                scrollable=True,
            ),
            dcc.Store(id="reader-source", data=None),
            dbc.Modal(
                [
                    dbc.ModalHeader(dbc.ModalTitle(id="note-modal-title")),
                    dbc.ModalBody(
                        [
                            html.Div(id="note-modal-list", className="mb-3"),
                            dbc.Textarea(id="note-input", placeholder="Add a note…", className="mb-2"),
                            dbc.Button("Add note", id="note-add", color="primary", size="sm"),
                        ]
                    ),
                ],
                id="note-modal",
                is_open=False,
                scrollable=True,
            ),
            dcc.Store(id="note-source", data=None),
            dcc.Store(id="notes-refresh", data={"ts": 0}),
            dbc.Modal(
                [
                    dbc.ModalHeader(dbc.ModalTitle(id="ft-exclude-title")),
                    dbc.ModalBody(
                        [
                            html.P(
                                "Why exclude at full text? This is reported in the PRISMA flow.",
                                className="text-muted small",
                            ),
                            dbc.Checklist(id="ft-exclude-choices", options=[], value=[]),
                            html.Hr(),
                            html.Small("Or add a new reason:", className="fw-bold"),
                            dbc.InputGroup(
                                [
                                    dbc.Input(id="ft-exclude-new", placeholder="New reason", size="sm"),
                                    dbc.Button("Add", id="ft-exclude-add", size="sm", color="secondary"),
                                ],
                                className="mt-1",
                            ),
                            html.Div(id="ft-exclude-feedback", className="small mt-2"),
                        ]
                    ),
                    dbc.ModalFooter(
                        [
                            dbc.Button("Cancel", id="ft-exclude-cancel", color="link"),
                            dbc.Button("Confirm exclude", id="ft-exclude-confirm", color="danger"),
                        ]
                    ),
                ],
                id="ft-exclude-modal",
                is_open=False,
            ),
            dcc.Store(id="ft-exclude-source", data=None),
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
            html.P(
                f"{cfg.project.type} • screening: {cfg.screening.workflow} • extraction: {cfg.extraction.workflow}",
                className="text-muted small mb-2",
            ),
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
    sources_view.register_callbacks(app)
    conflicts_view.register_callbacks(app)
    dashboard_view.register_callbacks(app)
    tags_view.register_callbacks(app)
    full_text_view.register_callbacks(app)
    ft_conflicts_view.register_callbacks(app)
    reports_view.register_callbacks(app)
    import_view.register_callbacks(app)
    duplicates_view.register_callbacks(app)
    database_view.register_callbacks(app)
    template_view.register_callbacks(app)
    protocol_view.register_callbacks(app)
    settings_view.register_callbacks(app)
    workflow_view.register_callbacks(app)

    @app.callback(
        Output("history-modal", "is_open"),
        Output("history-modal-title", "children"),
        Output("history-modal-body", "children"),
        Input({"type": "screen-history-btn", "source": ALL}, "n_clicks"),
        Input({"type": "conflict-history-btn", "source": ALL}, "n_clicks"),
        Input({"type": "ft-history-btn", "source": ALL}, "n_clicks"),
        Input({"type": "extract-history-btn", "source": ALL}, "n_clicks"),
        State("shared-reviewer", "value"),
        State("history-modal", "is_open"),
        prevent_initial_call=True,
    )
    def _open_history(_s, _c, _ft, _ex, reviewer, was_open):
        triggered = triggered_click_id()
        if triggered is None:
            return no_update, no_update, no_update

        sid = int(triggered["source"])
        btn_type = triggered.get("type", "")
        project_obj = get_project()
        db = project_obj.db
        src = db.get_source(sid)
        if src is None:
            return False, no_update, no_update

        if btn_type in ("screen-history-btn", "ft-history-btn", "extract-history-btn"):
            rid = (reviewer or "").strip() or None
            actions = db.get_screening_actions(sid, reviewer_id=rid)
            show_reviewer = False
            title = f"History — #{sid} (your timeline)"
        else:
            actions = db.get_screening_actions(sid)
            show_reviewer = True
            title = f"History — #{sid} (all reviewers)"

        body = _history_block(actions, src, show_reviewer=show_reviewer)
        return True, title, body

    @app.callback(
        Output("tag-modal", "is_open"),
        Output("tag-modal-title", "children"),
        Output("tag-modal-checklist", "options"),
        Output("tag-modal-checklist", "value"),
        Output("tag-modal-empty", "children"),
        Output("tag-modal-source", "data"),
        Input({"type": "screen-tag-btn", "source": ALL}, "n_clicks"),
        Input({"type": "ft-tag-btn", "source": ALL}, "n_clicks"),
        Input({"type": "extract-tag-btn", "source": ALL}, "n_clicks"),
        Input("tag-modal-done", "n_clicks"),
        State("tag-modal", "is_open"),
        prevent_initial_call=True,
    )
    def _open_tag_modal(_clicks, _ft_clicks, _ex_clicks, _done, was_open):
        if ctx.triggered_id == "tag-modal-done":
            return False, no_update, no_update, no_update, no_update, no_update
        triggered = triggered_click_id()
        if triggered is None:
            return no_update, no_update, no_update, no_update, no_update, no_update

        sid = int(triggered["source"])
        project_obj = get_project()
        db = project_obj.db
        src = db.get_source(sid)
        if src is None:
            return False, no_update, no_update, no_update, no_update, no_update

        all_tags = db.list_tags(project_obj.project_id)
        source_tags = db.get_tags_for_source(sid)
        options = [{"label": t["name"], "value": t["id"]} for t in all_tags]
        value = [t["id"] for t in source_tags]
        empty_msg = "" if all_tags else "No tags exist yet. Create some in the Tags tab."
        title = f"Tag #{sid}"
        return True, title, options, value, empty_msg, {"source_id": sid}

    @app.callback(
        Output("tags-refresh", "data", allow_duplicate=True),
        Output("tag-modal-checklist", "options", allow_duplicate=True),
        Output("tag-modal-checklist", "value", allow_duplicate=True),
        Output("tag-modal-create-feedback", "children"),
        Output("tag-modal-new-name", "value"),
        Input("tag-modal-create-apply", "n_clicks"),
        State("tag-modal-new-name", "value"),
        State("tag-modal-new-color", "value"),
        State("tag-modal-source", "data"),
        State("tag-modal-checklist", "value"),
        prevent_initial_call=True,
    )
    def _create_and_apply(_clicks, name, color, source_data, current_checked):
        if not source_data:
            return no_update, no_update, no_update, no_update, no_update
        sid = source_data.get("source_id")
        if sid is None or not name or not name.strip():
            return no_update, no_update, no_update, dbc.Alert("Name required", color="warning", className="mb-0"), no_update

        project_obj = get_project()
        db = project_obj.db
        clean_name = name.strip()

        try:
            tag_id = db.create_tag(project_obj.project_id, clean_name, color or "secondary")
        except DuplicateError:
            existing = db.get_tag_by_name(project_obj.project_id, clean_name)
            if not existing:
                return no_update, no_update, no_update, dbc.Alert("Couldn't resolve existing tag.", color="warning", className="mb-0"), no_update
            tag_id = existing["id"]

        db.tag_source(int(sid), int(tag_id))

        all_tags = db.list_tags(project_obj.project_id)
        options = [{"label": t["name"], "value": t["id"]} for t in all_tags]
        new_checked = list({*(current_checked or []), tag_id})

        import time
        return (
            {"ts": time.time()},
            options,
            new_checked,
            dbc.Alert(f"Created '{clean_name}' and applied.", color="success", className="mb-0"),
            "",
        )

    @app.callback(
        Output("tags-refresh", "data", allow_duplicate=True),
        Input("tag-modal-checklist", "value"),
        State("tag-modal-source", "data"),
        prevent_initial_call=True,
    )
    def _apply_tag_changes(checked, source_data):
        if not source_data:
            return no_update
        sid = source_data.get("source_id")
        if sid is None:
            return no_update
        db = get_project().db
        current = {t["id"] for t in db.get_tags_for_source(int(sid))}
        new = set(checked or [])
        to_add = new - current
        to_remove = current - new
        for tag_id in to_add:
            db.tag_source(int(sid), int(tag_id))
        for tag_id in to_remove:
            db.untag_source(int(sid), int(tag_id))
        if to_add or to_remove:
            import time
            return {"ts": time.time()}
        return no_update

    @app.callback(
        Output("reader-modal", "is_open"),
        Output("reader-modal-title", "children"),
        Output("reader-source", "data"),
        Output("reader-mode", "value"),
        Input({"type": "ft-read-btn", "source": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def _open_reader(_clicks):
        triggered = triggered_click_id()
        if triggered is None:
            return no_update, no_update, no_update, no_update
        sid = int(triggered["source"])
        src = get_project().db.get_source(sid)
        title = f"#{sid} — {src.title}" if src else f"#{sid}"
        return True, title, {"sid": sid}, "pdf"

    @app.callback(
        Output("reader-modal-body", "children"),
        Input("reader-mode", "value"),
        Input("reader-source", "data"),
        prevent_initial_call=True,
    )
    def _render_reader(mode, data):
        if not data or not isinstance(data, dict):
            return ""
        sid = data.get("sid")
        proj = get_project()
        src = proj.db.get_source(int(sid)) if sid is not None else None
        if src is None:
            return ""

        if mode == "pdf":
            if not src.pdf_path:
                return dbc.Alert(
                    "No PDF linked for this source. Run `ailr import-pdfs` to link one.",
                    color="warning",
                )
            return html.Iframe(
                src=f"/pdf/{sid}",
                style={"width": "100%", "height": "80vh", "border": "none"},
            )

        md_text = None
        if src.markdown_path:
            p = Path(src.markdown_path)
            if not p.is_absolute():
                p = proj.root / p
            if p.exists():
                md_text = p.read_text(encoding="utf-8")
        if not md_text:
            return dbc.Alert(
                "No markdown yet. Run `ailr preprocess` to convert this source's PDF.",
                color="info",
            )
        return dcc.Markdown(md_text)

    @app.callback(
        Output("note-modal", "is_open"),
        Output("note-modal-title", "children"),
        Output("note-source", "data"),
        Input({"type": "screen-note-btn", "source": ALL}, "n_clicks"),
        Input({"type": "ft-note-btn", "source": ALL}, "n_clicks"),
        Input({"type": "extract-note-btn", "source": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def _open_note_modal(_s, _ft, _ex):
        triggered = triggered_click_id()
        if triggered is None:
            return no_update, no_update, no_update
        sid = int(triggered["source"])
        return True, f"Notes — #{sid}", {"sid": sid}

    @app.callback(
        Output("note-modal-list", "children"),
        Input("note-source", "data"),
        Input("notes-refresh", "data"),
    )
    def _render_notes(data, _refresh):
        if not data or not isinstance(data, dict):
            return ""
        sid = data.get("sid")
        if sid is None:
            return ""
        notes = get_project().db.list_notes(int(sid))
        if not notes:
            return html.Small("No notes yet.", className="text-muted")
        return [
            dbc.Card(
                dbc.CardBody(
                    [
                        html.Div(n["text"], style={"whiteSpace": "pre-wrap"}),
                        html.Small(
                            f"{n.get('reviewer_id') or '?'} · {n.get('timestamp', '')}",
                            className="text-muted",
                        ),
                        dbc.Button(
                            "Delete",
                            id={"type": "note-delete", "note_id": n["id"]},
                            size="sm",
                            color="link",
                            className="p-0 ms-2 text-danger",
                        ),
                    ],
                    className="py-2 px-2",
                ),
                className="mb-2",
            )
            for n in notes
        ]

    @app.callback(
        Output("notes-refresh", "data"),
        Output("note-input", "value"),
        Input("note-add", "n_clicks"),
        Input({"type": "note-delete", "note_id": ALL}, "n_clicks"),
        State("note-input", "value"),
        State("note-source", "data"),
        State("shared-reviewer", "value"),
        prevent_initial_call=True,
    )
    def _mutate_notes(_add, _del, text, data, reviewer):
        import time
        db = get_project().db

        if ctx.triggered_id == "note-add":
            if not _add or not text or not text.strip() or not data:
                return no_update, no_update
            db.add_note(int(data["sid"]), (reviewer or "").strip() or None, text.strip())
            return {"ts": time.time()}, ""

        triggered = triggered_click_id()
        if triggered is not None and triggered.get("type") == "note-delete":
            db.delete_note(int(triggered["note_id"]))
            return {"ts": time.time()}, no_update

        return no_update, no_update

    return app


def main() -> None:
    app = build_app()
    port = int(os.environ.get("AILR_UI_PORT", "8050"))
    app.run(port=port, debug=False)


if __name__ == "__main__":
    main()
