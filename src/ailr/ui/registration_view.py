"""Protocol → Registration: PRISMA 2020 item 24 (register, number, protocol location, amendments).

The amendment log is not entered by hand — it is read back from the version snapshots the
criteria, variables, and prompt editors already write on every Save.
"""

from typing import Any

import dash_bootstrap_components as dbc
from dash import Input, Output, State, html, no_update

from ailr.core.config import save_registration
from ailr.ui._common import get_project, reload_project


def _field(label: str, cid: str, value: str, placeholder: str, help_text: str) -> Any:
    return html.Div(
        [
            dbc.Label(label, className="small fw-bold mb-0"),
            dbc.Input(id=cid, value=value, placeholder=placeholder, size="sm", debounce=True),
            html.Small(help_text, className="text-muted"),
        ],
        className="mb-3",
    )


def _amendment_table(rows: list[dict]) -> Any:
    amendments = [r for r in rows if r["is_amendment"]]
    if not amendments:
        return html.Small(
            "No amendments yet. Every time you save the criteria, the variables, or a stage's "
            "prompt, the change is snapshotted and the second and later versions appear here.",
            className="text-muted",
        )
    head = html.Thead(html.Tr([html.Th("Part"), html.Th("Version"), html.Th("When"), html.Th("Note")]))
    body = html.Tbody([
        html.Tr([
            html.Td(r["part"]),
            html.Td(r["version"]),
            html.Td(r["created_at"] or ""),
            html.Td(r["notes"] or html.Span("—", className="text-muted")),
        ])
        for r in amendments
    ])
    return dbc.Table([head, body], bordered=False, hover=True, size="sm")


def layout() -> Any:
    project = get_project()
    meta = project.config.project
    return html.Div(
        [
            html.P(
                "PRISMA asks every review to say where it is registered and where its protocol can "
                "be read — including saying plainly that it is neither, which is a valid answer. "
                "These go into the methods export.",
                className="text-muted small",
            ),
            dbc.Row(
                [
                    dbc.Col(_field("Register", "reg-registry", meta.registry, "PROSPERO / OSF / INPLASY — blank if unregistered",
                                   "The register the review is filed with."), width=4),
                    dbc.Col(_field("Registration number", "reg-number", meta.registration_number, "CRD42024xxxxxx",
                                   "As issued by the register."), width=4),
                    dbc.Col(_field("Protocol URL", "reg-protocol", meta.protocol_url, "https://…",
                                   "Where the protocol can be read."), width=4),
                ],
                className="g-3",
            ),
            dbc.Button("Save", id="reg-save", color="primary", size="sm"),
            html.Div(id="reg-save-fb", className="small mt-2"),
            html.Hr(className="my-4"),
            html.H6("Amendments"),
            html.P(
                "Changes to the protocol after its first version, taken from the saved revisions of "
                "the criteria, variables, and prompts.",
                className="text-muted small",
            ),
            html.Div(_amendment_table(project.db.list_amendments(project.project_id)), id="reg-amendments"),
        ]
    )


def register_callbacks(app: Any) -> None:
    @app.callback(
        Output("reg-save-fb", "children"),
        Input("reg-save", "n_clicks"),
        State("reg-registry", "value"),
        State("reg-number", "value"),
        State("reg-protocol", "value"),
        prevent_initial_call=True,
    )
    def _save(n, registry, number, protocol):
        if not n:
            return no_update
        project = get_project()
        try:
            save_registration(project.root, registry or "", number or "", protocol or "")
        except Exception as e:
            return dbc.Alert(f"Could not save: {e}", color="danger", className="py-1 mb-0")
        reload_project()
        return dbc.Alert("Saved.", color="success", className="py-1 mb-0")
