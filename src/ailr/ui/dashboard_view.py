"""Dashboard / Summary tab: project overview."""

from typing import Any, Optional

import dash_bootstrap_components as dbc
from dash import html

from ailr.ui._common import get_project


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
    abstract_conflicts = db.count_unresolved_screening_conflicts(pid, stage="abstract")
    ft_conflicts = db.count_unresolved_screening_conflicts(pid, stage="full_text")
    total_conflicts = abstract_conflicts + ft_conflicts
    api_summary = db.api_call_summary(pid)
    total_calls = sum((row.get("calls") or 0) for row in api_summary)
    total_in = sum((row.get("input_tokens") or 0) for row in api_summary)
    total_out = sum((row.get("output_tokens") or 0) for row in api_summary)

    abstract_sources_screened = db.count_sources_screened(pid, "human", stage="abstract")
    ft_human = db.screening_summary(pid, "human", stage="full_text")
    ft_sources_screened = db.count_sources_screened(pid, "human", stage="full_text")

    all_sources = db.list_sources(pid)
    all_ids = [s.id for s in all_sources if s.id is not None]
    with_md = sum(1 for s in all_sources if s.markdown_path)
    # Count extraction only among papers still confirmed for it (full-text includes with markdown),
    # so a paper moved back to full-text review stops counting as extracted until it's re-included.
    eligible_ext_ids = [s.id for s in db.list_full_text_final_includes_with_markdown(pid)]
    ai_extracted = len(db.sources_with_extraction(eligible_ext_ids, "ai"))
    human_extracted = len(db.sources_with_submission(eligible_ext_ids))

    my_done = len(db.get_decisions_by_reviewer(all_ids, rid)) if rid else 0

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
                className="text-white",
            ),
            bg="danger" if total_conflicts > 0 else "success",
            text="white",
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
            ],
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
            html.H4(f"Review Summary — {cfg.project.name}", className="mt-2"),
            html.P(
                f"{cfg.project.type} • screening: {cfg.screening.workflow} • extraction: {cfg.extraction.workflow}",
                className="text-muted small",
            ),
            html.Hr(),
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
) -> Any:
    body_children: list[Any] = [
        dbc.Row(
            [
                dbc.Col(
                    [
                        html.H6(title, className="fw-bold mb-3"),
                        html.Div(
                            [
                                html.Span(main_metric, style={"fontSize": "2rem", "fontWeight": "bold"}),
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
    if text:
        card_kwargs["inverse"] = text == "white"
    return dbc.Card(dbc.CardBody(body_children), **card_kwargs)
