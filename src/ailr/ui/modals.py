"""The always-mounted modals: history, tags, study grouping, the reader, notes, and the
full-text exclusion reason.

They live outside the tab content so any page can open one by emitting a button with the agreed
pattern id (e.g. {"type": "screen-note-btn", "source": sid}) — screening, full text, and extraction
all reuse the same six. The exclusion modal is the odd one out: its layout belongs here so it is
always mounted, but its callbacks sit with the full-text review that owns the workflow.
"""

from pathlib import Path
from typing import Any

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update

from ailr.exceptions import DuplicateError
from ailr.ui._common import triggered_click_id
from ailr.ui._project import get_project

from ailr.ui.screen_view import _history_block
from ailr.ui.tags_view import TAG_COLOR_OPTIONS


def layout() -> list[Any]:
    return [
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
                dbc.ModalHeader(dbc.ModalTitle(id="study-modal-title")),
                dbc.ModalBody(
                    [
                        html.P(
                            "Several reports of one study (main paper, protocol, secondary analysis) "
                            "count as one study in the PRISMA flow. Pick the report that represents "
                            "the study; leave blank if this report stands alone.",
                            className="text-muted small",
                        ),
                        dcc.Dropdown(
                            id="study-modal-pick",
                            options=[],
                            value=None,
                            placeholder="Search by author / title / id…",
                        ),
                        html.Div(id="study-modal-current", className="small mt-3"),
                    ]
                ),
                dbc.ModalFooter(
                    [
                        dbc.Button("Detach", id="study-modal-clear", color="link", className="text-danger"),
                        dbc.Button("Save", id="study-modal-save", color="primary"),
                    ]
                ),
            ],
            id="study-modal",
            is_open=False,
            size="lg",
        ),
        dcc.Store(id="study-modal-source", data=None),
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
    ]


def register_callbacks(app: Any) -> None:
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
        Output("study-modal", "is_open"),
        Output("study-modal-title", "children"),
        Output("study-modal-pick", "options"),
        Output("study-modal-pick", "value"),
        Output("study-modal-current", "children"),
        Output("study-modal-source", "data"),
        Input({"type": "ft-study-btn", "source": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def _open_study_modal(_clicks):
        triggered = triggered_click_id()
        if triggered is None:
            return (no_update,) * 6

        sid = int(triggered["source"])
        project_obj = get_project()
        db = project_obj.db
        src = db.get_source(sid)
        if src is None:
            return False, no_update, no_update, no_update, no_update, no_update

        # Candidates are the other full-text candidates; a report cannot group with itself.
        options = []
        for s in db.list_full_text_candidates(
            project_obj.project_id, workflow=project_obj.config.screening_workflow("abstract")
        ):
            if s.id == sid:
                continue
            author = s.authors[0].split(",")[0].strip() if s.authors else ""
            head = f"{author} {s.year}".strip() if s.year else author
            options.append({"label": f"{head} — {s.title or '(untitled)'} (#{s.id})", "value": s.id})

        companions = db.list_study_companions([sid]).get(sid, [])
        current = (
            html.Span([
                "Currently grouped with: ",
                html.Strong(", ".join(f"#{c['id']}" for c in companions)),
            ])
            if companions
            else html.Span("This report is currently its own study.", className="text-muted")
        )
        return True, f"Same study as… (#{sid})", options, src.study_group_id, current, {"source_id": sid}

    @app.callback(
        Output("study-modal", "is_open", allow_duplicate=True),
        Output("ft-refresh", "data", allow_duplicate=True),
        Input("study-modal-save", "n_clicks"),
        Input("study-modal-clear", "n_clicks"),
        State("study-modal-pick", "value"),
        State("study-modal-source", "data"),
        prevent_initial_call=True,
    )
    def _save_study_group(_save, _clear, picked, source_data):
        if not source_data:
            return no_update, no_update
        sid = source_data.get("source_id")
        if sid is None:
            return no_update, no_update
        primary = None if ctx.triggered_id == "study-modal-clear" else (int(picked) if picked else None)
        try:
            get_project().db.set_study_group(int(sid), primary)
        except Exception:
            return no_update, no_update
        return False, {"ts": time.time()}

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
