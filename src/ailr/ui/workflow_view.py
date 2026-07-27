"""Per-stage workflow settings (Protocol -> Workflow) plus each stage's prompt/AI tabs."""

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


def protocol_layout() -> Any:
    """All three stage workflows on one screen. They live on Protocol because who screens each
    stage is a protocol decision — pre-registered and reported under PRISMA — not a preference,
    and because the couplings below are only visible with the three side by side."""
    from ailr.ui.extract_view import extraction_workflow_block

    cfg = get_project().config
    return html.Div(
        [
            html.P(
                "Who does the work at each stage. The three stages are set independently: the common "
                "design is AI-assisted at title/abstract, where the volume is, and two humans at full "
                "text, where the stakes are.",
                className="text-muted small",
            ),
            dbc.Label("Abstract screening workflow", className="fw-bold"),
            dbc.Select(id="workflow-select", options=_OPTIONS, value=cfg.screening.workflow, size="sm"),
            html.Ul(
                [
                    html.Li([html.Strong("assisted: "), "AI and one human each decide blind; disagreements go to Conflicts."], className="small"),
                    html.Li([html.Strong("independent: "), "two humans each decide blind; their disagreements go to Conflicts."], className="small"),
                ],
                className="mt-1",
            ),
            html.Div(dbc.Button("Save", id="workflow-save", color="primary", size="sm"), className="mt-1"),
            html.Div(id="workflow-feedback", className="small mt-2"),
            html.Hr(className="my-3"),
            dbc.Label("Full-text screening workflow", className="fw-bold"),
            dbc.Select(
                id="ft-workflow-select",
                options=_OPTIONS,
                value=cfg.screening_workflow("full_text"),
                size="sm",
            ),
            html.Div(id="ft-workflow-feedback", className="small mt-2"),
            html.Hr(className="my-3"),
            *extraction_workflow_block(),
            dbc.Alert(
                [
                    html.Strong("How full-text screening and extraction interact. "),
                    "There is no separate AI full-text screening run: the AI's full-text verdict is derived "
                    "from the per-criterion flag_check verdicts produced during AI extraction. So ",
                    html.Strong("assisted"),
                    " full-text screening needs AI extraction to have run — without it the AI has no vote and "
                    "the stage behaves as a single human. Under ",
                    html.Strong("independent"),
                    " the two humans decide on their own and AI extraction is not required first.",
                ],
                color="light", className="small py-2 mt-3 mb-0",
            ),
        ]
    )


def layout(section: str = "abstract") -> Any:
    if section == "full_text":
        from ailr.ui import template_view
        from ailr.ui.extract_view import ai_extraction_panel
        from ailr.ui.full_text_view import pdf_tools_panel

        prep_tab = [
            html.P(
                "Get PDFs and their markdown ready for full-text review + extraction. "
                "Who screens and who extracts is set on Protocol → Workflow.",
                className="text-muted small",
            ),
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
                dbc.Tab(html.Div(prep_tab, className="pt-3"), label="Preparation", tab_id="wf-prep"),
                dbc.Tab(html.Div(prompt_tab, className="pt-3"), label="Prompt", tab_id="wf-prompt"),
                dbc.Tab(html.Div(calibration_view.layout("extraction"), className="pt-3"), label="Calibration", tab_id="wf-cal"),
                dbc.Tab(html.Div(extraction_tab, className="pt-3"), label="AI extraction", tab_id="wf-extract"),
            ],
            active_tab="wf-prep",
        )

    from ailr.ui.screen_view import ai_screening_panel, screening_prompt_panel

    prompt_tab = [
        html.P("Edit the screening prompt and additional instructions. The criteria are shared with extraction and edited on the Protocol page; who screens this stage is set on Protocol → Workflow.", className="text-muted small"),
        *screening_prompt_panel(),
    ]
    ai_tab = [
        html.P("Run AI on the abstracts, or import results you ran yourself.", className="text-muted small"),
        *ai_screening_panel(),
    ]
    return dbc.Tabs(
        [
            dbc.Tab(html.Div(prompt_tab, className="pt-3"), label="Prompt", tab_id="wf-prompt"),
            dbc.Tab(html.Div(calibration_view.layout("abstract"), className="pt-3"), label="Calibration", tab_id="wf-cal"),
            dbc.Tab(html.Div(ai_tab, className="pt-3"), label="AI screening", tab_id="wf-ai"),
        ],
        active_tab="wf-prompt",
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
        return dbc.Alert(f"Saved: abstract screening workflow = {value}.", color="success", className="mb-0 py-1")

    @app.callback(
        Output("ft-workflow-feedback", "children"),
        Input("ft-workflow-select", "value"),
        prevent_initial_call=True,
    )
    def _save_ft(value):
        if value not in ("assisted", "independent"):
            return no_update
        project = get_project()
        if value == project.config.screening_workflow("full_text"):
            return no_update
        save_stage_workflow(project.root, "full_text_screening", value)
        reload_project()
        return f"saved: full-text screening workflow = {value}"
