"""Consensus: reconcile two independent extractions of the same paper into one final record.

Reached from Full-text review -> "To reconcile" -> "Open comparison". Fields the reviewers agree
on are carried over untouched; only disagreements ask for a decision, which is either one
reviewer's answer (value and quote together) or a value the adjudicator types instead.

The result is written as `extractor_type='consensus'` rows, which is what the "final" exports read.
"""

import json
from typing import Any

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update

from ailr.extraction import FieldSpec, compose_schema
from ailr.reviewers import ExtractionResult
from ailr.ui._common import format_authors, get_project
from ailr.ui.extract_view import reader_body

_APP_CHROME_PX = 115
_CUSTOM = "__custom__"


def layout() -> Any:
    scroll_pane = {"flex": "1 1 0", "minWidth": 0, "minHeight": 0, "overflowY": "auto", "paddingRight": "6px"}
    return html.Div(
        [
            dbc.Row(
                [
                    dbc.Col(dbc.Button("← Back to full-text review", id="cons-back", color="link", size="sm", className="p-0"), width="auto"),
                    dbc.Col(
                        dbc.RadioItems(
                            id="cons-reader-mode",
                            options=[{"label": "PDF", "value": "pdf"}, {"label": "Markdown", "value": "md"}],
                            value="pdf", inline=True,
                        ),
                        width="auto",
                    ),
                    dbc.Col(
                        [
                            dbc.Button("Undo consensus", id="cons-undo", color="link", size="sm", className="text-danger me-2"),
                            dbc.Button("Save consensus", id="cons-save", color="primary", size="sm"),
                        ],
                        width="auto",
                        className="ms-auto",
                    ),
                ],
                className="align-items-center g-2 mb-1",
            ),
            html.Div(id="cons-feedback", className="small mb-1"),
            html.Hr(className="my-1"),
            html.Div(
                [
                    html.Div(
                        html.Div(id="cons-reader", style={"flex": "1 1 0", "minHeight": 0, "display": "flex", "flexDirection": "column"}),
                        style={"flex": "1 1 0", "minWidth": 0, "display": "flex", "flexDirection": "column", "paddingRight": "8px"},
                    ),
                    html.Div(
                        [
                            html.Div(id="cons-source-card"),
                            html.Div(id="cons-summary", className="mt-2"),
                            html.Div(id="cons-body", className="mt-2"),
                        ],
                        style=scroll_pane,
                    ),
                ],
                style={"display": "flex", "flex": "1 1 auto", "minHeight": 0, "gap": "8px"},
            ),
        ],
        style={"display": "flex", "flexDirection": "column", "height": f"calc(100vh - {_APP_CHROME_PX}px)"},
    )


# ── Comparing two reviewers' answers ────────────────────────────────────────────────────────


def _norm(value: Any) -> str:
    """Comparison key. Scalars compare as trimmed text; a plain list compares regardless of the
    order the reviewers happened to type it in; anything nested compares structurally."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list) and all(not isinstance(v, (dict, list)) for v in value):
        return json.dumps(sorted(str(v).strip() for v in value), ensure_ascii=False)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _latest_per_field(rows: list[dict]) -> dict[str, dict[str, dict]]:
    """{extractor_id: {field_name: row}} — rows arrive ordered by id, so later rows win.
    Reserved `_`-prefixed rows (submit markers, flag checks) are not extracted values."""
    out: dict[str, dict[str, dict]] = {}
    for r in rows:
        if str(r["field_name"]).startswith("_"):
            continue
        out.setdefault(r.get("extractor_id") or "", {})[r["field_name"]] = r
    return out


def _cell(row: dict | None) -> tuple[Any, Any]:
    if row is None:
        return None, None
    return row.get("value"), row.get("source_quote")


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _render_value(field: FieldSpec, value: Any) -> Any:
    """Read-only rendering of one reviewer's answer. Repeating groups and nested objects render as
    a small table so the difference is visible even though they are adjudicated whole."""
    if _is_blank(value):
        return html.Span("(not filled in)", className="text-muted fst-italic")
    if field.type == "list" and field.item_type == "object" and isinstance(value, list):
        subs = [s.name for s in (field.item_fields or [])] or sorted({k for it in value if isinstance(it, dict) for k in it})
        head = html.Thead(html.Tr([html.Th(s) for s in subs]))
        body = html.Tbody([
            html.Tr([html.Td(_scalar_text(it.get(s)) if isinstance(it, dict) else "") for s in subs])
            for it in value
        ])
        return html.Div(
            dbc.Table([head, body], bordered=True, size="sm", className="mb-0", style={"fontSize": "0.75rem"}),
            style={"overflowX": "auto"},
        )
    if isinstance(value, dict):
        return html.Div(
            [html.Div([html.Strong(f"{k}: ", className="small"), html.Span(_scalar_text(v), className="small")]) for k, v in value.items()]
        )
    if isinstance(value, list):
        return html.Span("; ".join(_scalar_text(v) for v in value))
    return html.Span(_scalar_text(value))


def _scalar_text(v: Any) -> str:
    """Leaf display; values may still be wrapped as {value, quote} inside nested structures."""
    if isinstance(v, dict) and "value" in v:
        v = v.get("value")
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _agreed_block(entries: list[tuple[FieldSpec, Any]]) -> Any:
    rows = [
        html.Tr([
            html.Td(html.Strong(f.name), style={"width": "30%"}),
            html.Td(_render_value(f, v)),
        ])
        for f, v in entries
    ]
    return html.Details(
        [
            html.Summary(html.Small(f"Show the {len(entries)} agreed field(s)", className="text-muted")),
            dbc.Table([html.Tbody(rows)], bordered=False, size="sm", className="mt-2"),
        ],
        className="mb-3",
    )


def _disagreement_row(field: FieldSpec, answers: list[tuple[str, Any, Any]], editable: bool) -> Any:
    """One field the reviewers differ on. `answers` is [(reviewer_id, value, quote)] in submit order.

    Distinct answers become radio options labelled with who gave them; identical answers from
    several reviewers collapse onto one option. `editable` adds a free-text alternative — offered
    for leaf fields only, since typing a repeating group by hand is not a sane interaction.
    """
    options: list[dict] = []
    seen: dict[str, list[str]] = {}
    for rid, value, _quote in answers:
        seen.setdefault(_norm(value), []).append(rid)
    for key, rids in seen.items():
        value = next(v for r, v, _q in answers if _norm(v) == key and r == rids[0])
        options.append({
            "label": html.Span([
                html.Span(", ".join(rids), className="badge bg-secondary me-2"),
                _render_value(field, value),
            ]),
            "value": key,
        })
    if editable:
        options.append({"label": html.Span("Something else — type it below", className="text-muted"), "value": _CUSTOM})

    quotes = [
        html.Details(
            [html.Summary(html.Small(f"{rid}'s quote", className="text-muted")),
             html.Small(f"“{quote}”", className="text-muted d-block fst-italic ms-3")],
        )
        for rid, _v, quote in answers if quote
    ]

    custom_widgets: list[Any] = []
    if editable:
        custom_widgets = [
            dbc.Input(id={"type": "cons-custom", "field": field.name}, placeholder="a different final value", size="sm", className="mt-1"),
            dbc.Textarea(id={"type": "cons-custom-quote", "field": field.name}, placeholder="supporting quote (optional)", style={"height": "40px"}, className="mt-1"),
        ]

    return dbc.Card(
        dbc.CardBody(
            [
                html.Div([html.Strong(field.name), html.Small(f"  ({field.type})", className="text-muted ms-1")]),
                html.P(field.description, className="text-muted small mb-1") if field.description else None,
                dbc.RadioItems(id={"type": "cons-pick", "field": field.name}, options=options, value=None, className="small"),
                *custom_widgets,
                *quotes,
            ]
        ),
        className="mb-2",
        style={"borderLeft": "3px solid #f0ad4e"},
    )


def _compare(project: Any, sid: int) -> tuple[list[tuple[FieldSpec, Any, Any]], list[Any], dict]:
    """(agreed, disagreement cards, state) for one source.

    agreed  = [(field, value, quote)] carried straight into the consensus record
    state   = {field_name: {norm_key: {"value":…, "quote":…}}} so Save can turn a radio pick
              back into the value and quote it came from.
    """
    db = project.db
    fields = compose_schema(project.root / project.config.extraction.schema_path)
    per_reviewer = _latest_per_field(db.list_extractions(sid, extractor_type="human"))
    submitters = [r for r in db.extraction_submitters(sid) if r in per_reviewer]

    agreed: list[tuple[FieldSpec, Any, Any]] = []
    cards: list[Any] = []
    state: dict[str, dict] = {}
    for field in fields:
        answers = [(rid, *_cell(per_reviewer.get(rid, {}).get(field.name))) for rid in submitters]
        keys = {_norm(v) for _rid, v, _q in answers}
        if len(keys) <= 1:
            value, quote = (answers[0][1], answers[0][2]) if answers else (None, None)
            agreed.append((field, value, quote))
            continue
        editable = field.type not in ("object",) and not (field.type == "list" and field.item_type == "object")
        cards.append(_disagreement_row(field, answers, editable))
        state[field.name] = {
            _norm(v): {"value": v, "quote": q} for _rid, v, q in answers
        }
    return agreed, cards, state


# ── Callbacks ───────────────────────────────────────────────────────────────────────────────


def register_callbacks(app: Any) -> None:
    @app.callback(
        Output("tabs", "data", allow_duplicate=True),
        Input("cons-back", "n_clicks"),
        prevent_initial_call=True,
    )
    def _back(n):
        return "full_text" if n else no_update

    @app.callback(
        Output("cons-reader", "children"),
        Input("cons-store", "data"),
        Input("cons-reader-mode", "value"),
    )
    def _reader(store, mode):
        sid = (store or {}).get("sid")
        if not sid:
            return html.Small("Open a paper from the Full-text review page.", className="text-muted")
        return reader_body(get_project(), int(sid), mode)

    @app.callback(
        Output("cons-source-card", "children"),
        Output("cons-summary", "children"),
        Output("cons-body", "children"),
        Output("cons-state", "data"),
        Input("cons-store", "data"),
        Input("cons-refresh", "data"),
    )
    def _render(store, _refresh):
        sid = (store or {}).get("sid")
        if not sid:
            return "", "", html.Div("Open a paper from the Full-text review page.", className="text-muted"), {}
        project = get_project()
        src = project.db.get_source(int(sid))
        if src is None:
            return "", "", html.Div("That paper is no longer available.", className="text-muted"), {}

        agreed, cards, state = _compare(project, int(sid))
        submitters = project.db.extraction_submitters(int(sid))
        existing = project.db.consensus_adjudicator(int(sid))

        card = dbc.Card(dbc.CardBody([
            html.H6(f"#{src.id}  {src.title}", className="mb-1"),
            html.Small(format_authors(src.authors) if src.authors else "", className="text-muted d-block"),
            html.Small(f"Extracted independently by: {', '.join(submitters)}", className="text-muted d-block mt-1"),
        ]), className="mb-2")

        total = len(agreed) + len(cards)
        summary: list[Any] = [
            dbc.Alert(
                f"{len(agreed)} of {total} variables agree and are carried over as they are. "
                + (f"{len(cards)} need a decision." if cards else "Nothing left to decide."),
                color="warning" if cards else "success",
                className="py-2 mb-2",
            )
        ]
        if existing:
            summary.append(html.Small(f"Already adjudicated by {existing} — saving again replaces it.",
                                      className="text-muted d-block mb-2"))
        if agreed:
            summary.append(_agreed_block([(f, v) for f, v, _q in agreed]))

        body = cards or [html.Div("The reviewers agree on every variable — click Save consensus to record it.",
                                  className="text-muted small")]
        return card, summary, body, state

    @app.callback(
        Output("cons-feedback", "children"),
        Output("cons-refresh", "data", allow_duplicate=True),
        Output("tabs", "data", allow_duplicate=True),
        Input("cons-save", "n_clicks"),
        Input("cons-undo", "n_clicks"),
        State("cons-store", "data"),
        State("cons-state", "data"),
        State("shared-reviewer", "value"),
        State({"type": "cons-pick", "field": ALL}, "value"),
        State({"type": "cons-pick", "field": ALL}, "id"),
        State({"type": "cons-custom", "field": ALL}, "value"),
        State({"type": "cons-custom", "field": ALL}, "id"),
        State({"type": "cons-custom-quote", "field": ALL}, "value"),
        State({"type": "cons-custom-quote", "field": ALL}, "id"),
        prevent_initial_call=True,
    )
    def _save(save_n, undo_n, store, state, reviewer, picks, pick_ids, customs, custom_ids, cquotes, cquote_ids):
        trigger = ctx.triggered_id
        sid = (store or {}).get("sid")
        if not sid:
            return no_update, no_update, no_update
        project = get_project()
        rid = (reviewer or "").strip()

        if trigger == "cons-undo" and undo_n:
            project.db.delete_consensus(int(sid))
            return dbc.Alert("Consensus removed — this paper is back in the queue.", color="secondary",
                             className="py-1 mb-0"), {"ts": (undo_n or 0)}, no_update

        if trigger != "cons-save" or not save_n:
            return no_update, no_update, no_update
        if not rid:
            return dbc.Alert("Enter your reviewer ID at the top first.", color="warning", className="py-1 mb-0"), no_update, no_update

        chosen = {i["field"]: v for i, v in zip(pick_ids, picks)}
        custom = {i["field"]: v for i, v in zip(custom_ids, customs)}
        cquote = {i["field"]: v for i, v in zip(cquote_ids, cquotes)}

        undecided = [f for f, v in chosen.items() if not v or (v == _CUSTOM and not str(custom.get(f) or "").strip())]
        if undecided:
            return dbc.Alert(
                f"Still undecided: {', '.join(undecided)}. Pick an answer for each (or type one).",
                color="warning", className="py-1 mb-0",
            ), no_update, no_update

        agreed, _cards, _state = _compare(project, int(sid))
        results = [
            ExtractionResult(extractor_type="consensus", extractor_id=rid, source_id=int(sid),
                             field_name=f.name, value=value, source_quote=quote, prompt_version="consensus")
            for f, value, quote in agreed
        ]
        for fname, key in chosen.items():
            if key == _CUSTOM:
                value, quote = custom.get(fname), cquote.get(fname)
            else:
                picked = (state or {}).get(fname, {}).get(key) or {}
                value, quote = picked.get("value"), picked.get("quote")
            results.append(
                ExtractionResult(extractor_type="consensus", extractor_id=rid, source_id=int(sid),
                                 field_name=fname, value=value, source_quote=quote, prompt_version="consensus")
            )
        project.db.save_consensus(int(sid), rid, results)
        return no_update, {"ts": (save_n or 0)}, "full_text"
