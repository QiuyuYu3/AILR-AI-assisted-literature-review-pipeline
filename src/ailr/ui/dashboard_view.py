"""Dashboard / Summary tab: project overview."""

from typing import Any, Optional

import dash_bootstrap_components as dbc
from dash import html

from ailr.core.config import team_size_for
from ailr.ui._project import get_project



def layout(reviewer: str = "") -> Any:
    # Rendered by the main tab router (app._render_tab), which passes the current
    # reviewer as State. No global-Input callback here — that would fire cross-tab
    # (dashboard-content is only mounted on this tab) and crash the renderer.
    return html.Div(_build_content(reviewer), id="dashboard-content")


def register_callbacks(app: Any) -> None:
    pass


def _build_content(reviewer: Optional[str]) -> Any:
    project = get_project()
    db = project.db
    cfg = project.config
    pid = project.project_id
    rid = (reviewer or "").strip()

    total_sources = db.count_sources(pid)
    ai_counts = db.screening_summary(pid, "ai")
    human_counts = db.screening_summary(pid, "human")
    # Count conflicts with the same rule the Conflicts tab uses for this mode: AI-vs-human in
    # assisted, human-vs-human in independent.
    def _count_conflicts(stage: str) -> int:
        counter = (
            db.count_unresolved_assisted_conflicts
            if cfg.screening_workflow(stage) == "assisted"
            else db.count_unresolved_screening_conflicts
        )
        return counter(pid, stage=stage)

    abstract_conflicts = _count_conflicts("abstract")
    ft_conflicts = _count_conflicts("full_text")
    total_conflicts = abstract_conflicts + ft_conflicts
    api_summary = db.api_call_summary(pid)
    # Mock runs fabricate token counts; exclude them so this reflects real, billable API usage.
    billed = [row for row in api_summary if (row.get("provider") or "").lower() != "mock"]
    total_calls = sum((row.get("calls") or 0) for row in billed)
    total_in = sum((row.get("input_tokens") or 0) for row in billed)
    total_out = sum((row.get("output_tokens") or 0) for row in billed)

    abstract_sources_screened = db.count_sources_screened(pid, "human", stage="abstract")
    ft_human = db.screening_summary(pid, "human", stage="full_text")
    ft_sources_screened = db.count_sources_screened(pid, "human", stage="full_text")

    with_md = db.count_sources_with_markdown(pid)
    # Count extraction only among papers still confirmed for it (full-text includes with markdown),
    # so a paper moved back to full-text review stops counting as extracted until it's re-included.
    _ft_conflict_ids = db.unresolved_conflict_ids(pid, cfg.screening_workflow("full_text"), stage="full_text")
    _ft_team_size = team_size_for(cfg.screening_workflow("full_text"))
    eligible_ext_ids = [
        s.id for s in db.list_full_text_final_includes_with_markdown(pid, team_size=_ft_team_size)
        if s.id not in _ft_conflict_ids
    ]
    ai_extracted = len(db.sources_with_extraction(eligible_ext_ids, "ai"))
    human_extracted = len(db.sources_with_submission(eligible_ext_ids))
    # Only independent extraction produces papers waiting on an adjudicated consensus.
    awaiting_consensus = (
        len(db.sources_needing_consensus(eligible_ext_ids))
        if cfg.extraction.workflow == "independent" else 0
    )

    my_done = db.count_reviewer_decisions(pid, rid) if rid else 0

    cards = [
        _stage_card(
            title="Import references",
            main_metric=f"{total_sources}",
            main_label="sources in database",
            sub_metrics=[],
        ),
        _stage_card(
            title="Title & abstract screening",
            main_metric=f"{sum(human_counts.values())}",
            main_label="human decisions",
            sub_metrics=[
                ("include", human_counts["include"], "success"),
                ("exclude", human_counts["exclude"], "danger"),
                ("uncertain", human_counts["uncertain"], "warning"),
            ],
            extra=html.Small(
                f"across {abstract_sources_screened} unique source(s)  •  "
                f"AI: {sum(ai_counts.values())} decisions  •  "
                f"You: {my_done} / {total_sources} reviewed",
                className="text-muted",
            ),
        ),
        _stage_card(
            title="Conflicts to resolve",
            main_metric=f"{total_conflicts}",
            main_label="unresolved",
            sub_metrics=[],
            extra=html.Small(
                f"abstract: {abstract_conflicts}  •  full-text: {ft_conflicts}",
                className="text-muted",
            ),
            # Colour only the border + the number (amber = needs attention), not the whole card,
            # so it draws the eye without the glaring full-red block.
            bg="warning" if total_conflicts > 0 else "success",
            outline=True,
        ),
        _stage_card(
            title="Full-text screening",
            main_metric=f"{sum(ft_human.values())}",
            main_label="full-text decisions",
            sub_metrics=[
                ("include", ft_human["include"], "success"),
                ("exclude", ft_human["exclude"], "danger"),
                ("uncertain", ft_human["uncertain"], "warning"),
            ],
            extra=html.Small(
                f"across {ft_sources_screened} unique source(s)  •  "
                f"{with_md} source(s) have markdown ready",
                className="text-muted",
            ),
        ),
        _stage_card(
            title="Full-text extraction",
            main_metric=f"{ai_extracted}",
            main_label="AI-extracted sources",
            sub_metrics=[
                ("with markdown", with_md, "info"),
                ("verified by human", human_extracted, "primary"),
            ] + ([("awaiting reconciliation", awaiting_consensus, "warning")] if awaiting_consensus else []),
        ),
        _stage_card(
            title="API usage",
            main_metric=f"{total_in + total_out:,}",
            main_label="tokens (in + out)",
            sub_metrics=[],
            extra=html.Small(f"{total_calls} LLM calls • {total_in:,} in / {total_out:,} out", className="text-muted"),
        ),
    ]

    rows: list[Any] = [dbc.Row(dbc.Col(c, width=12)) for c in cards]

    return dbc.Container(
        [
            *rows,
            html.Hr(),
            html.P(
                "PRISMA flow and methods skeleton: run `ailr export <project> --format prisma` "
                "or `--format methods` on the command line.",
                className="text-muted small",
            ),
        ],
        fluid=True,
    )


def _stage_card(
    title: str,
    main_metric: str,
    main_label: str,
    sub_metrics: list[tuple[str, int, str]],
    extra: Any = None,
    bg: Optional[str] = None,
    text: Optional[str] = None,
    outline: bool = False,
) -> Any:
    body_children: list[Any] = [
        dbc.Row(
            [
                dbc.Col(
                    [
                        html.H6(title, className="fw-bold mb-3"),
                        html.Div(
                            [
                                html.Span(main_metric, className=(f"text-{bg}" if outline and bg else None),
                                          style={"fontSize": "2rem", "fontWeight": "bold"}),
                                html.Span(f"  {main_label}", className="ms-2"),
                            ]
                        ),
                    ],
                    width=6,
                ),
                dbc.Col(
                    [
                        html.Div(
                            [
                                dbc.Badge(
                                    f"{label}: {value}",
                                    color=color,
                                    className="me-2",
                                )
                                for (label, value, color) in sub_metrics
                            ]
                        ),
                    ],
                    width=6,
                    className="d-flex align-items-end",
                ),
            ]
        ),
    ]
    if extra is not None:
        body_children.append(html.Div(extra, className="mt-2"))
    card_kwargs: dict = {"className": "mb-3"}
    if bg:
        card_kwargs["color"] = bg
        if outline:
            card_kwargs["outline"] = True  # colour the border only, not the whole card
    if text and not outline:
        card_kwargs["inverse"] = text == "white"
    return dbc.Card(dbc.CardBody(body_children), **card_kwargs)
