"""Protocol page: the review's shared definitions — criteria + extraction variables."""

from typing import Any

import dash_bootstrap_components as dbc
from dash import html

from ailr.ui import criteria_view, registration_view, template_view, workflow_view


def layout() -> Any:
    return html.Div(
        [
            html.H4("Protocol"),
            html.P(
                "Define once, used everywhere: the criteria drive both screening and extraction; the "
                "variables are the data-extraction form; the workflows say who does the work at each "
                "stage. Each stage's prompt is configured on its own page.",
                className="text-muted small",
            ),
            dbc.Tabs(
                [
                    dbc.Tab(html.Div(criteria_view.layout(), className="pt-3"), label="Criteria", tab_id="proto-criteria"),
                    dbc.Tab(html.Div(template_view.variables_layout(), className="pt-3"), label="Variables", tab_id="proto-variables"),
                    dbc.Tab(html.Div(workflow_view.protocol_layout(), className="pt-3"), label="Workflow", tab_id="proto-workflow"),
                    dbc.Tab(html.Div(registration_view.layout(), className="pt-3"), label="Registration", tab_id="proto-registration"),
                ],
                active_tab="proto-criteria",
            ),
        ]
    )


def register_callbacks(app: Any) -> None:
    criteria_view.register_callbacks(app)
    registration_view.register_callbacks(app)
