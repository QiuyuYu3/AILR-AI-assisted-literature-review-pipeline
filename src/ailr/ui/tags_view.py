"""Tags tab: create / rename / recolor / delete tags."""

import time
from typing import Any

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, ctx, html, no_update

from ailr.exceptions import DuplicateError
from ailr.ui._common import get_project, triggered_click_id

TAG_COLOR_OPTIONS = [
    {"label": "Gray", "value": "secondary"},
    {"label": "Blue", "value": "primary"},
    {"label": "Green", "value": "success"},
    {"label": "Red", "value": "danger"},
    {"label": "Yellow", "value": "warning"},
    {"label": "Cyan", "value": "info"},
    {"label": "Dark", "value": "dark"},
]


def layout() -> Any:
    return dbc.Container(
        [
            html.H5("Tags", className="mt-2"),
            html.P(
                "Create reusable labels for sources. Apply them in bulk from the Sources tab, "
                "or filter screening by tag from the Screening sidebar.",
                className="text-muted small",
            ),
            dbc.Card(
                dbc.CardBody(
                    [
                        html.H6("Create new tag", className="fw-bold"),
                        dbc.Row(
                            [
                                dbc.Col(
                                    dbc.Input(
                                        id="tags-new-name",
                                        placeholder="Tag name (e.g., Modality-Audio)",
                                        size="sm",
                                    ),
                                    width=5,
                                ),
                                dbc.Col(
                                    dbc.Select(
                                        id="tags-new-color",
                                        options=TAG_COLOR_OPTIONS,
                                        value="secondary",
                                        size="sm",
                                    ),
                                    width=3,
                                ),
                                dbc.Col(
                                    dbc.Button(
                                        "Create",
                                        id="tags-create-btn",
                                        color="primary",
                                        size="sm",
                                    ),
                                    width=2,
                                ),
                            ]
                        ),
                        html.Div(id="tags-create-feedback", className="mt-2 small"),
                    ]
                ),
                className="mb-3",
            ),
            html.H6("Existing tags", className="fw-bold"),
            html.Div(id="tags-list", children=_render_initial_list()),
            dbc.Modal(
                [
                    dbc.ModalHeader(dbc.ModalTitle("Delete tag?")),
                    dbc.ModalBody(id="tags-delete-body"),
                    dbc.ModalFooter(
                        [
                            dbc.Button("Cancel", id="tags-delete-cancel", color="secondary"),
                            dbc.Button("Delete", id="tags-delete-confirm", color="danger"),
                        ]
                    ),
                ],
                id="tags-delete-modal",
                is_open=False,
            ),
        ],
        fluid=True,
    )


def _render_initial_list() -> Any:
    project = get_project()
    tags = project.db.list_tags(project.project_id)
    if not tags:
        return dbc.Alert("No tags yet. Create one above.", color="info")
    return [_tag_row(t) for t in tags]


def register_callbacks(app: Any) -> None:
    @app.callback(
        Output("tags-refresh", "data", allow_duplicate=True),
        Output("tags-create-feedback", "children"),
        Output("tags-new-name", "value"),
        Input("tags-create-btn", "n_clicks"),
        State("tags-new-name", "value"),
        State("tags-new-color", "value"),
        prevent_initial_call=True,
    )
    def _create(_clicks, name, color):
        if not name or not name.strip():
            return no_update, dbc.Alert("Name is required.", color="warning", className="mb-0"), no_update
        project = get_project()
        try:
            project.db.create_tag(project.project_id, name.strip(), color or "secondary")
        except DuplicateError as e:
            return no_update, dbc.Alert(str(e), color="warning", className="mb-0"), no_update
        return {"ts": time.time()}, dbc.Alert(f"Created tag {name!r}.", color="success", className="mb-0"), ""

    @app.callback(
        Output("tags-refresh", "data", allow_duplicate=True),
        Input({"type": "tag-save", "id": ALL}, "n_clicks"),
        State({"type": "tag-rename", "id": ALL}, "value"),
        State({"type": "tag-rename", "id": ALL}, "id"),
        State({"type": "tag-color", "id": ALL}, "value"),
        State({"type": "tag-color", "id": ALL}, "id"),
        prevent_initial_call=True,
    )
    def _save(_clicks, names, name_ids, colors, color_ids):
        triggered = triggered_click_id()
        if triggered is None:
            return no_update
        tag_id = int(triggered["id"])
        name = None
        for n, nid in zip(names, name_ids):
            if isinstance(nid, dict) and nid.get("id") == tag_id:
                name = n
                break
        color = None
        for c, cid in zip(colors, color_ids):
            if isinstance(cid, dict) and cid.get("id") == tag_id:
                color = c
                break
        db = get_project().db
        try:
            db.update_tag(tag_id, name=name if (name and name.strip()) else None, color=color)
        except DuplicateError:
            pass
        return {"ts": time.time()}

    @app.callback(
        Output("tags-delete-modal", "is_open"),
        Output("tags-delete-body", "children"),
        Output("tags-delete-pending", "data"),
        Input({"type": "tag-delete", "id": ALL}, "n_clicks"),
        Input("tags-delete-cancel", "n_clicks"),
        Input("tags-delete-confirm", "n_clicks"),
        State("tags-delete-pending", "data"),
        prevent_initial_call=True,
    )
    def _delete_flow(_open_clicks, _cancel, _confirm, pending):
        if ctx.triggered_id == "tags-delete-cancel":
            return False, "", None

        if ctx.triggered_id == "tags-delete-confirm":
            if pending and pending.get("id"):
                db = get_project().db
                db.delete_tag(int(pending["id"]))
            return False, "", None

        triggered = triggered_click_id()  # a tag-delete (open) button on a re-rendering list
        if triggered is not None and triggered.get("type") == "tag-delete":
            tag_id = int(triggered["id"])
            db = get_project().db
            row = db._conn.execute("SELECT name FROM tags WHERE id = ?", (tag_id,)).fetchone()
            name = row["name"] if row else f"id {tag_id}"
            body = html.P(
                [
                    "Delete tag ",
                    html.Strong(name),
                    "? This removes it from all sources currently tagged with it (the sources themselves stay).",
                ]
            )
            return True, body, {"id": tag_id}

        return no_update, no_update, no_update

    @app.callback(
        Output("tags-list", "children"),
        Input("tags-refresh", "data"),
        prevent_initial_call=True,
    )
    def _render_list(_refresh):
        return _render_initial_list()


def _tag_row(tag: dict) -> Any:
    return dbc.Card(
        dbc.CardBody(
            dbc.Row(
                [
                    dbc.Col(
                        [
                            dbc.Badge(
                                tag["name"],
                                color=tag.get("color") or "secondary",
                                className="me-2",
                                pill=True,
                            ),
                            html.Small(
                                f"{tag.get('source_count', 0)} source(s)",
                                className="text-muted",
                            ),
                        ],
                        width=3,
                    ),
                    dbc.Col(
                        dbc.Input(
                            id={"type": "tag-rename", "id": tag["id"]},
                            value=tag["name"],
                            size="sm",
                        ),
                        width=4,
                    ),
                    dbc.Col(
                        dbc.Select(
                            id={"type": "tag-color", "id": tag["id"]},
                            options=TAG_COLOR_OPTIONS,
                            value=tag.get("color") or "secondary",
                            size="sm",
                        ),
                        width=2,
                    ),
                    dbc.Col(
                        [
                            dbc.Button(
                                "Save",
                                id={"type": "tag-save", "id": tag["id"]},
                                size="sm",
                                color="primary",
                                className="me-1",
                            ),
                            dbc.Button(
                                "Delete",
                                id={"type": "tag-delete", "id": tag["id"]},
                                size="sm",
                                color="danger",
                                outline=True,
                            ),
                        ],
                        width=3,
                    ),
                ],
                align="center",
            ),
            className="py-2",
        ),
        className="mb-2",
    )
