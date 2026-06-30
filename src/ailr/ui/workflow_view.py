"""Workflow tab: configure the screening workflow (shared by abstract + full-text)."""

from typing import Any

import dash_bootstrap_components as dbc
from dash import Input, Output, State, html, no_update

from ailr.core.config import save_stage_workflow
from ailr.ui import calibration_view
from ailr.ui._common import get_project, reload_project

_OPTIONS = [
    {"label": "assisted — AI + 1 human, both blinded (PRISMA-trAIce)", "value": "assisted"},
    {"label": "independent — 2 humans, both blinded (Cochrane)", "value": "independent"},
]


def layout(section: str = "abstract") -> Any:
    project = get_project()

    if section == "full_text":
        from ailr.ui import template_view
        from ailr.ui.extract_view import ai_extraction_panel, extraction_workflow_block
        from ailr.ui.full_text_view import pdf_tools_panel

        workflow_tab = [
            html.P(
                "Full-text screening uses the shared screening workflow (set on Abstract → Workflow). "
                "Here: how data extraction is done, plus PDF preparation.",
                className="text-muted small",
            ),
            *extraction_workflow_block(),
            html.Hr(className="my-3"),
            html.H5("Full-text preparation", className="mb-2"),
            html.P("Get PDFs and their markdown ready for full-text review + extraction.", className="text-muted small"),
            *pdf_tools_panel(),
        ]
        prompt_tab = [
            html.P(
                "Edit the extraction prompt and additional instructions for this stage. The criteria and the "
                "extraction variables are shared definitions — edit them on the Protocol page.",
                className="text-muted small",
            ),
            template_view.prompt_layout(),
        ]
        extraction_tab = [
            html.P("Run AI extraction on included papers, or import results you ran externally (use 'Run externally' under Import to copy the prompt and download the JSON template).", className="text-muted small"),
            *ai_extraction_panel(),
        ]
        return dbc.Tabs(
            [
                dbc.Tab(html.Div(workflow_tab, className="pt-3"), label="Workflow", tab_id="wf-settings"),
                dbc.Tab(html.Div(prompt_tab, className="pt-3"), label="Prompt", tab_id="wf-prompt"),
                dbc.Tab(html.Div(calibration_view.layout("extraction"), className="pt-3"), label="Calibration", tab_id="wf-cal"),
                dbc.Tab(html.Div(extraction_tab, className="pt-3"), label="AI extraction", tab_id="wf-extract"),
            ],
            active_tab="wf-settings",
        )

    from ailr.ui.screen_view import ai_screening_panel, screening_prompt_panel

    workflow_tab = [
        html.P(
            "Who screens and how (AI + 1 human, or 2 humans). This collaboration mode is shared by "
            "abstract and full-text screening. What each stage reads (abstract vs. full text) and "
            "data extraction are configured separately.",
            className="text-muted small",
        ),
        dbc.Label("Screening workflow", className="fw-bold"),
        dbc.Select(id="workflow-select", options=_OPTIONS, value=project.config.screening.workflow),
        html.Ul(
            [
                html.Li([html.Strong("assisted: "), "AI and one human each decide blind; disagreements go to Conflicts."], className="small"),
                html.Li([html.Strong("independent: "), "two humans each decide blind; their disagreements go to Conflicts."], className="small"),
            ],
            className="mt-2",
        ),
        html.Div(dbc.Button("Save", id="workflow-save", color="primary", size="sm"), className="mt-1"),
        html.Div(id="workflow-feedback", className="small mt-2"),
    ]
    prompt_tab = [
        html.P("Edit the screening prompt and additional instructions. The criteria are shared with extraction and edited on the Protocol page.", className="text-muted small"),
        *screening_prompt_panel(),
    ]
    ai_tab = [
        html.P("Run AI on the abstracts, or import results you ran yourself.", className="text-muted small"),
        *ai_screening_panel(),
    ]
    return dbc.Tabs(
        [
            dbc.Tab(html.Div(workflow_tab, className="pt-3"), label="Workflow", tab_id="wf-settings"),
            dbc.Tab(html.Div(prompt_tab, className="pt-3"), label="Prompt", tab_id="wf-prompt"),
            dbc.Tab(html.Div(ai_tab, className="pt-3"), label="AI screening", tab_id="wf-ai"),
            dbc.Tab(html.Div(calibration_view.layout("abstract"), className="pt-3"), label="Calibration", tab_id="wf-cal"),
        ],
        active_tab="wf-settings",
    )


def register_callbacks(app: Any) -> None:
    @app.callback(
        Output("workflow-feedback", "children"),
        Input("workflow-save", "n_clicks"),
        State("workflow-select", "value"),
        prevent_initial_call=True,
    )
    def _save(n, value):
        if not n or value not in ("assisted", "independent"):
            return no_update
        project = get_project()
        save_stage_workflow(project.root, "screening", value)
        reload_project()
        return dbc.Alert(f"Saved: screening workflow = {value}.", color="success", className="mb-0 py-1")
