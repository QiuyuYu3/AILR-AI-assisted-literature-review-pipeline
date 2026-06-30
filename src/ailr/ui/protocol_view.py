"""Protocol page: the review's shared definitions — criteria + extraction variables."""

from typing import Any

import dash_bootstrap_components as dbc
from dash import html

from ailr.ui import criteria_view, template_view


def layout() -> Any:
    return html.Div(
        [
            html.H4("Protocol"),
            html.P(
                "Define once, used everywhere: the criteria drive both screening and extraction; the "
                "variables are the data-extraction form. Each stage's prompt is configured on its own Workflow page.",
                className="text-muted small",
            ),
            dbc.Tabs(
                [
                    dbc.Tab(html.Div(criteria_view.layout(), className="pt-3"), label="Criteria", tab_id="proto-criteria"),
                    dbc.Tab(html.Div(template_view.variables_layout(), className="pt-3"), label="Variables", tab_id="proto-variables"),
                ],
                active_tab="proto-criteria",
            ),
        ]
    )


def register_callbacks(app: Any) -> None:
    criteria_view.register_callbacks(app)
