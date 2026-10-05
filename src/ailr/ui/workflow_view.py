"""Per-stage workflow settings (Protocol -> Workflow) plus each stage's prompt/AI tabs."""

from typing import Any

import dash_bootstrap_components as dbc
from dash import Input, Output, State, html, no_update

from ailr.core.config import ai_votes_for, save_stage_workflow
from ailr.ui import calibration_view
from ailr.ui._project import get_project, reload_project

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
            html.Div(_flag_check_warning(cfg), id="ft-workflow-feedback", className="small mt-2"),
            html.Hr(className="my-3"),
            *extraction_workflow_block(),
            dbc.Alert(
                [
                    html.Strong("How full-text screening and extraction interact. "),
                    "There is no separate AI full-text screening run: the AI's full-text verdict is derived "
                    "from the per-criterion flag_check verdicts produced during AI extraction. So ",
                    html.Strong("assisted"),
                    " full-text screening needs AI extraction to have run: until it has, a paper the human has "
                    "reviewed waits for the AI's verdict and is not settled. Under ",
                    html.Strong("independent"),
                    " the two humans decide on their own and AI extraction is not required first.",
                ],
                color="light", className="small py-2 mt-3 mb-0",
            ),
        ]
    )


def _flag_check_warning(cfg) -> Any:
    """Assisted full text takes the AI's vote from flag_check, so with it off no paper can settle."""
    if not ai_votes_for(cfg.screening_workflow("full_text")) or cfg.extraction.flag_check:
        return None
    return dbc.Alert(
        "Full-text screening is assisted, but extraction.flag_check is off in lit_review.yaml, so AI "
        "extraction gives no full-text verdict and every paper would wait for one. Turn flag_check on, "
        "or make full-text screening independent.",
        color="warning", className="py-1 mb-0",
    )


def _prompt_accordion(*sections: tuple[str, Any]) -> Any:
    """The stage prompt and the cross-check prompt that judges it, on one page. An accordion rather
    than nested tabs: the stage's own prompt is the one people edit, the checker's is rarely touched."""
    return dbc.Accordion(
        [
            dbc.AccordionItem(html.Div(body, className="pt-1"), title=title, item_id=f"wf-prompt-{i}")
            for i, (title, body) in enumerate(sections)
        ],
        active_item="wf-prompt-0",
        flush=True,
        className="mt-2",
    )


def layout(section: str = "abstract") -> Any:
    if section == "full_text":
        from ailr.ui import template_view
        from ailr.ui.extract_view import ai_extraction_panel
        from ailr.ui.preprocess_view import pdf_tools_panel

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
            _prompt_accordion(
                ("Extraction prompt", template_view.prompt_layout()),
                ("Cross-check prompt (optional)", template_view.crosscheck_prompt_panel()),
            ),
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

    from ailr.ui.screen_view import (
        ai_screening_panel,
        screening_crosscheck_prompt_panel,
        screening_prompt_panel,
    )

    prompt_tab = [
        html.P("Edit the screening prompt and additional instructions. The criteria are shared with extraction and edited on the Protocol page; who screens this stage is set on Protocol → Workflow.", className="text-muted small"),
        _prompt_accordion(
            ("Screening prompt", screening_prompt_panel()),
            ("Cross-check prompt (optional)", screening_crosscheck_prompt_panel()),
        ),
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
        # Full text follows this setting unless it has its own, so the warning can change here too.
        warning = _flag_check_warning(reload_project().config)
        return [dbc.Alert(f"Saved: abstract screening workflow = {value}.", color="success", className="mb-0 py-1"), warning]

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
        return [f"saved: full-text screening workflow = {value}", _flag_check_warning(reload_project().config)]
