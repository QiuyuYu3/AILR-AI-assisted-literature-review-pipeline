"""Shared engine for the abstract and full-text conflict/reconciliation tabs.

Both tabs are the same screen (adjudicate human-vs-human or AI-vs-human disagreements,
record a final decision) differing only in stage, component-id prefixes, and a few
abstract-only extras (DOI link, abstract collapse, history button). A ConflictConfig
captures those differences; conflicts_view / ft_conflicts_view are thin wrappers.
"""

import time
from dataclasses import dataclass
from typing import Any

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, html, no_update

from ailr.core.source import Source
from ailr.ui._common import _short_author_year, get_project, triggered_click_id

_DECISION_COLORS = {
    "include": "success",
    "exclude": "danger",
    "uncertain": "warning",
}

_FLAG_COLORS = {"PASS": "success", "FAIL": "danger", "UNCERTAIN": "warning"}


@dataclass(frozen=True)
class ConflictConfig:
    prefix: str               # pattern-id 'type' prefix, e.g. "conflict" / "ft-conflict"
    store_prefix: str         # static component-id prefix, e.g. "conflicts" / "ft-conflicts"
    stage: str                # query stage: "abstract" / "full_text"
    reconcile_list_stage: str # list_reconciliations stage: "screening" / "full_text_screening"
    title: str
    assisted_desc: str
    independent_desc: str
    no_conflicts_msg: str
    votes_label: str
    blank_reasonings: tuple[str, ...]
    show_flag_check: bool       # full-text: show AI flag_check verdicts
    show_abstract_extras: bool  # abstract: DOI link, abstract collapse, history button


def ai_detail_block(ai: dict, flag_check: Any = None) -> Any:
    """Full AI screening rationale: decision + confidence + matched criteria + reasoning + evidence quotes.
    For full-text decisions, pass flag_check to show the per-criterion verdicts (the real rationale)."""
    color = _DECISION_COLORS.get(ai.get("decision", ""), "secondary")
    rows: list[Any] = [
        html.Div(
            [
                dbc.Badge(ai.get("decision", "").upper(), color=color, className="me-2"),
                html.Strong("AI", className="me-2"),
                html.Small(f"confidence {ai['confidence']}" if ai.get("confidence") is not None else "", className="text-muted"),
            ]
        )
    ]
    reasoning = (ai.get("reasoning") or "").strip()
    if reasoning and reasoning != "(derived from extraction flag_check)":
        rows.append(html.Div(reasoning, className="small text-muted"))
    if flag_check:
        rows.append(
            html.Div(
                [
                    html.Div(
                        [
                            dbc.Badge((f.get("verdict") or "").upper(), color=_FLAG_COLORS.get((f.get("verdict") or "").upper(), "secondary"), className="me-2"),
                            html.Strong(str(f.get("criterion_id", "?")), className="small me-2"),
                            html.Span(f.get("reason") or "", className="text-muted small"),
                        ],
                        className="mb-1",
                    )
                    for f in flag_check
                ],
                className="mt-1",
            )
        )
    if ai.get("matched_criteria"):
        rows.append(html.Div("Criteria: " + ", ".join(str(c) for c in ai["matched_criteria"]), className="small text-muted"))
    if ai.get("evidence_quotes"):
        rows.append(
            html.Details(
                [html.Summary("AI evidence quotes", className="small"), html.Ul([html.Li(str(q), className="small") for q in ai["evidence_quotes"]])]
            )
        )
    return html.Div(rows, className="mb-1")


def initial_payload(cfg: ConflictConfig) -> tuple[Any, str, Any]:
    project = get_project()
    db = project.db
    if project.config.screening.workflow == "assisted":
        conflicts = db.list_assisted_conflicts(project.project_id, stage=cfg.stage)
    else:
        conflicts = db.list_screening_conflicts(project.project_id, stage=cfg.stage)
    if conflicts:
        cards = [_conflict_card(cfg, s, db) for s in conflicts]
        count_text = f"{len(conflicts)} unresolved conflict(s)"
    else:
        cards = [dbc.Alert(cfg.no_conflicts_msg, color="success")]
        count_text = "0 unresolved"
    resolved = db.list_reconciliations(project.project_id, stage=cfg.reconcile_list_stage, limit=10)
    resolved_ui = _resolved_list(cfg, resolved) if resolved else html.P("(none yet)", className="small text-muted")
    return cards, count_text, resolved_ui


def build_layout(cfg: ConflictConfig) -> Any:
    cards, count_text, resolved_ui = initial_payload(cfg)
    assisted = get_project().config.screening.workflow == "assisted"
    return dbc.Row(
        [
            dbc.Col(
                [
                    html.H6(cfg.title, className="fw-bold"),
                    html.P(
                        cfg.assisted_desc if assisted else cfg.independent_desc,
                        className="small text-muted",
                    ),
                    html.Hr(),
                    html.Div(count_text, id=f"{cfg.store_prefix}-counts", className="small text-muted"),
                ],
                width=3,
            ),
            dbc.Col(
                [
                    html.Div(cards, id=f"{cfg.store_prefix}-cards"),
                    html.Hr(className="mt-4"),
                    html.H6("Recently resolved", className="fw-bold"),
                    html.Div(resolved_ui, id=f"{cfg.store_prefix}-resolved"),
                ],
                width=9,
            ),
        ]
    )


def register_callbacks(app: Any, cfg: ConflictConfig) -> None:
    decide_type = f"{cfg.prefix}-decide"
    rationale_type = f"{cfg.prefix}-rationale"
    undo_type = f"{cfg.prefix}-undo"
    refresh_id = f"{cfg.store_prefix}-refresh"

    @app.callback(
        Output(refresh_id, "data"),
        Input({"type": decide_type, "source": ALL, "decision": ALL}, "n_clicks"),
        State("shared-reviewer", "value"),
        State({"type": rationale_type, "source": ALL}, "value"),
        State({"type": rationale_type, "source": ALL}, "id"),
        prevent_initial_call=True,
    )
    def _on_resolve(_clicks, reviewer, rationales, rationale_ids):
        rid = (reviewer or "").strip()
        triggered = triggered_click_id()  # the actually-clicked button (not the first re-rendered one)
        if triggered is None or not rid or triggered.get("type") != decide_type:
            return no_update

        source_id = int(triggered["source"])
        decision = triggered["decision"]
        rationale = None
        for r_val, r_id in zip(rationales or [], rationale_ids or []):
            if isinstance(r_id, dict) and r_id.get("source") == source_id:
                rationale = (r_val or "").strip() or None
                break

        db = get_project().db
        db.insert_screening_reconciliation(source_id, decision, rid, rationale, stage=cfg.stage)
        db.insert_screening_action(source_id, rid, action="reconcile", decision=decision)
        return {"ts": time.time()}

    if cfg.show_abstract_extras:
        @app.callback(
            Output({"type": f"{cfg.prefix}-abstract-body", "source": ALL}, "is_open"),
            Input({"type": f"{cfg.prefix}-abstract-btn", "source": ALL}, "n_clicks"),
            State({"type": f"{cfg.prefix}-abstract-body", "source": ALL}, "is_open"),
            prevent_initial_call=True,
        )
        def _toggle_abstract(clicks, is_open_list):
            from dash import callback_context
            triggered = triggered_click_id()
            if triggered is None:
                return no_update
            target_sid = triggered.get("source")
            ids = [t["id"] for t in callback_context.inputs_list[0]]
            out = []
            for i, comp_id in enumerate(ids):
                if comp_id.get("source") == target_sid:
                    out.append(not is_open_list[i])
                else:
                    out.append(is_open_list[i])
            return out

    @app.callback(
        Output(refresh_id, "data", allow_duplicate=True),
        Input({"type": undo_type, "rec_id": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def _on_undo(_clicks):
        triggered = triggered_click_id()
        if triggered is None:
            return no_update
        rec_id = int(triggered["rec_id"])
        db = get_project().db
        # Fetch the row to learn source_id + adjudicator before deleting (for audit trail).
        row = db._conn.execute(
            "SELECT source_id, adjudicator FROM reconciliations WHERE id = ?",
            (rec_id,),
        ).fetchone()
        db.delete_reconciliation(rec_id)
        if row:
            db.insert_screening_action(row["source_id"], row["adjudicator"], action="reconcile_undo")
        return {"ts": time.time()}

    @app.callback(
        Output(f"{cfg.store_prefix}-cards", "children"),
        Output(f"{cfg.store_prefix}-counts", "children"),
        Output(f"{cfg.store_prefix}-resolved", "children"),
        Input(refresh_id, "data"),
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
        return initial_payload(cfg)


def _conflict_card(cfg: ConflictConfig, src: Source, db: Any) -> Any:
    sid = src.id
    decisions = db.get_human_decisions(sid, stage=cfg.stage)

    vote_rows: list[Any] = []
    ai = db.get_latest_ai_decision(sid, stage=cfg.stage)
    if ai is not None:
        flag_check = db.get_flag_check(sid, "ai") if cfg.show_flag_check else None
        vote_rows.append(ai_detail_block(ai, flag_check=flag_check))
    for d in decisions:
        color = _DECISION_COLORS.get(d["decision"], "secondary")
        reasoning = (d.get("reasoning") or "").strip()
        if reasoning in cfg.blank_reasonings:
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
            dbc.Button("Include", id={"type": f"{cfg.prefix}-decide", "source": sid, "decision": "include"}, color="success", size="sm"),
            dbc.Button("Exclude", id={"type": f"{cfg.prefix}-decide", "source": sid, "decision": "exclude"}, color="danger", size="sm"),
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

    body: list[Any] = [header, html.H6(src.title, className="mb-1"), meta_line]
    if cfg.show_abstract_extras:
        body.extend(_abstract_extras(cfg, src, sid))
    body.extend(
        [
            html.Hr(),
            html.Div(cfg.votes_label, className="small fw-bold mb-1"),
            html.Div(vote_rows, className="mb-2"),
            html.Hr(),
            html.Div("Final decision (you as adjudicator)", className="small fw-bold mb-1"),
            dbc.Input(
                id={"type": f"{cfg.prefix}-rationale", "source": sid},
                placeholder="Adjudicator rationale (optional)",
                size="sm",
                className="mb-2",
            ),
            final_btns,
        ]
    )
    return dbc.Card(dbc.CardBody(body), className="mb-3")


def _abstract_extras(cfg: ConflictConfig, src: Source, sid: Any) -> list[Any]:
    doi_el: Any = None
    if src.doi:
        doi_el = html.Div(
            html.A(f"DOI: {src.doi}", href=f"https://doi.org/{src.doi}", target="_blank", className="small")
        )
    abstract_btn = html.Div(
        dbc.Button("Abstract ▼", id={"type": f"{cfg.prefix}-abstract-btn", "source": sid}, size="sm", color="link", className="p-0"),
        className="mt-1",
    )
    abstract_body = dbc.Collapse(
        html.P(src.abstract or "(no abstract)", className="mt-2 small", style={"whiteSpace": "pre-wrap"}),
        id={"type": f"{cfg.prefix}-abstract-body", "source": sid},
        is_open=False,
    )
    history_btn = html.Div(
        dbc.Button("History", id={"type": f"{cfg.prefix}-history-btn", "source": sid}, size="sm", color="link", className="p-0"),
        className="mt-1",
    )
    return [doi_el, abstract_btn, abstract_body, history_btn]


def _resolved_list(cfg: ConflictConfig, reconciliations: list[dict]) -> Any:
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
                                        id={"type": f"{cfg.prefix}-undo", "rec_id": r["id"]},
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
