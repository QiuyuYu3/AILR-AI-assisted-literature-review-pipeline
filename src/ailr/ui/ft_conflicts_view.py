"""Full-text conflicts: stage='full_text' conflicts between human reviewers."""

import time
from typing import Any

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, ctx, html, no_update

from ailr.core.source import Source
from ailr.ui._common import get_project
from ailr.ui.conflicts_view import ai_detail_block

_DECISION_COLORS = {
    "include": "success",
    "exclude": "danger",
    "uncertain": "warning",
}


def _initial_payload() -> tuple[Any, str, Any]:
    project = get_project()
    db = project.db
    if project.config.screening.workflow == "assisted":
        conflicts = db.list_assisted_conflicts(project.project_id, stage="full_text")
    else:
        conflicts = db.list_screening_conflicts(project.project_id, stage="full_text")
    if conflicts:
        cards = [_conflict_card(s, db) for s in conflicts]
        count_text = f"{len(conflicts)} unresolved conflict(s)"
    else:
        cards = [dbc.Alert("No unresolved full-text conflicts. ✓", color="success")]
        count_text = "0 unresolved"
    resolved = db.list_reconciliations(project.project_id, stage="full_text_screening", limit=10)
    resolved_ui = (
        _resolved_list(resolved) if resolved else html.P("(none yet)", className="small text-muted")
    )
    return cards, count_text, resolved_ui


def layout() -> Any:
    cards, count_text, resolved_ui = _initial_payload()
    return dbc.Row(
        [
            dbc.Col(
                [
                    html.H6("Resolve full-text conflicts", className="fw-bold"),
                    html.P(
                        (
                            "Assisted mode: AI (flag_check) vs human disagreements at the full-text stage."
                            if get_project().config.screening.workflow == "assisted"
                            else "Independent mode: two human reviewers disagreed at the full-text stage."
                        ),
                        className="small text-muted",
                    ),
                    html.Hr(),
                    html.Div(count_text, id="ft-conflicts-counts", className="small text-muted"),
                ],
                width=3,
            ),
            dbc.Col(
                [
                    html.Div(cards, id="ft-conflicts-cards"),
                    html.Hr(className="mt-4"),
                    html.H6("Recently resolved", className="fw-bold"),
                    html.Div(resolved_ui, id="ft-conflicts-resolved"),
                ],
                width=9,
            ),
        ]
    )


def register_callbacks(app: Any) -> None:
    @app.callback(
        Output("ft-conflicts-refresh", "data"),
        Input({"type": "ft-conflict-decide", "source": ALL, "decision": ALL}, "n_clicks"),
        State("shared-reviewer", "value"),
        State({"type": "ft-conflict-rationale", "source": ALL}, "value"),
        State({"type": "ft-conflict-rationale", "source": ALL}, "id"),
        prevent_initial_call=True,
    )
    def _on_resolve(_clicks, reviewer, rationales, rationale_ids):
        triggered = ctx.triggered_id
        rid = (reviewer or "").strip()
        if triggered is None or not rid:
            return no_update
        any_click = any(c for c in (ctx.triggered or []) if c.get("value"))
        if not any_click:
            return no_update
        if not isinstance(triggered, dict) or triggered.get("type") != "ft-conflict-decide":
            return no_update

        source_id = int(triggered["source"])
        decision = triggered["decision"]
        rationale = None
        for r_val, r_id in zip(rationales or [], rationale_ids or []):
            if isinstance(r_id, dict) and r_id.get("source") == source_id:
                rationale = (r_val or "").strip() or None
                break

        db = get_project().db
        db.insert_screening_reconciliation(source_id, decision, rid, rationale, stage="full_text")
        db.insert_screening_action(source_id, rid, action="reconcile", decision=decision)
        return {"ts": time.time()}

    @app.callback(
        Output("ft-conflicts-refresh", "data", allow_duplicate=True),
        Input({"type": "ft-conflict-undo", "rec_id": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def _on_undo(_clicks):
        triggered = ctx.triggered_id
        if triggered is None or not isinstance(triggered, dict):
            return no_update
        any_click = any(c for c in (ctx.triggered or []) if c.get("value"))
        if not any_click:
            return no_update
        rec_id = int(triggered["rec_id"])
        db = get_project().db
        row = db._conn.execute(
            "SELECT source_id, adjudicator FROM reconciliations WHERE id = ?", (rec_id,)
        ).fetchone()
        db.delete_reconciliation(rec_id)
        if row:
            db.insert_screening_action(row["source_id"], row["adjudicator"], action="reconcile_undo")
        return {"ts": time.time()}

    @app.callback(
        Output("ft-conflicts-cards", "children"),
        Output("ft-conflicts-counts", "children"),
        Output("ft-conflicts-resolved", "children"),
        Input("ft-conflicts-refresh", "data"),
        Input("shared-reviewer", "value"),
        prevent_initial_call=True,
    )
    def _render(_refresh, reviewer):
        rid = (reviewer or "").strip()
        if not rid:
            return (
                [dbc.Alert("Enter your reviewer ID above to act as adjudicator.", color="info")],
                "",
                "",
            )
        return _initial_payload()


def _conflict_card(src: Source, db: Any) -> Any:
    sid = src.id
    decisions = db.get_human_decisions(sid, stage="full_text")

    vote_rows: list[Any] = []
    ai = db.get_latest_ai_decision(sid, stage="full_text")
    if ai is not None:
        vote_rows.append(ai_detail_block(ai, flag_check=db.get_flag_check(sid, "ai")))
    for d in decisions:
        color = _DECISION_COLORS.get(d["decision"], "secondary")
        reasoning = (d.get("reasoning") or "").strip()
        if reasoning in ("(full-text review)", "(inline screening)"):
            reasoning = ""
        vote_rows.append(
            html.Div(
                [
                    dbc.Badge(d["decision"].upper(), color=color, className="me-2"),
                    html.Strong(d["reviewer_id"], className="me-2"),
                    html.Small(d.get("timestamp", ""), className="text-muted me-2"),
                    html.Span(reasoning, className="text-muted small"),
                ],
                className="mb-1",
            )
        )

    final_btns = dbc.ButtonGroup(
        [
            dbc.Button("Include", id={"type": "ft-conflict-decide", "source": sid, "decision": "include"}, color="success", size="sm"),
            dbc.Button("Exclude", id={"type": "ft-conflict-decide", "source": sid, "decision": "exclude"}, color="danger", size="sm"),
        ]
    )

    header = html.Div(
        [
            html.Strong(f"#{sid}  ", className="text-muted"),
            html.Span(_short_author_year(src), className="text-muted me-2"),
        ]
    )
    meta_parts: list[str] = []
    if src.journal:
        meta_parts.append(src.journal)
    if src.year:
        meta_parts.append(str(src.year))
    meta_line = html.P(" • ".join(meta_parts), className="text-muted small mb-1")

    return dbc.Card(
        dbc.CardBody(
            [
                header,
                html.H6(src.title, className="mb-1"),
                meta_line,
                html.Hr(),
                html.Div("Reviewer votes (full-text stage)", className="small fw-bold mb-1"),
                html.Div(vote_rows, className="mb-2"),
                html.Hr(),
                html.Div("Final decision (you as adjudicator)", className="small fw-bold mb-1"),
                dbc.Input(
                    id={"type": "ft-conflict-rationale", "source": sid},
                    placeholder="Adjudicator rationale (optional)",
                    size="sm",
                    className="mb-2",
                ),
                final_btns,
            ]
        ),
        className="mb-3",
    )


def _short_author_year(src: Source) -> str:
    if not src.authors:
        return f"({src.year})" if src.year else ""
    first = src.authors[0].split(",")[0].strip()
    return f"{first} {src.year}" if src.year else first


def _resolved_list(reconciliations: list[dict]) -> Any:
    rows: list[Any] = []
    for r in reconciliations:
        color = _DECISION_COLORS.get(r.get("final_value", ""), "secondary")
        rationale = (r.get("rationale") or "").strip()
        rows.append(
            dbc.Card(
                dbc.CardBody(
                    [
                        dbc.Row(
                            [
                                dbc.Col(
                                    [
                                        html.Span(f"#{r['source_id']} ", className="text-muted me-2"),
                                        dbc.Badge(
                                            (r.get("final_value") or "").upper(),
                                            color=color,
                                            className="me-2",
                                        ),
                                        html.Span(r.get("title") or "", className="small"),
                                        html.Br(),
                                        html.Small(
                                            f"by {r.get('adjudicator', '?')} • {r.get('timestamp', '')}"
                                            + (f" — {rationale}" if rationale else ""),
                                            className="text-muted",
                                        ),
                                    ],
                                    width=10,
                                ),
                                dbc.Col(
                                    dbc.Button(
                                        "Undo",
                                        id={"type": "ft-conflict-undo", "rec_id": r["id"]},
                                        color="link",
                                        size="sm",
                                    ),
                                    width=2,
                                    className="text-end",
                                ),
                            ]
                        ),
                    ],
                    className="py-2",
                ),
                className="mb-2",
            )
        )
    return rows
