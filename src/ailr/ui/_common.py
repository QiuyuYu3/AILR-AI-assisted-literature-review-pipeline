"""Presentation helpers shared by the Dash views: small components, formatting, and the
callback-context helper every re-rendering list depends on.

Project loading and the files that belong to a project live in _project.py.
"""

import json
from typing import Optional

import dash_bootstrap_components as dbc
from dash import ctx, dcc, html

from ailr.ui._project import read_criteria_set


def help_icon(text: str, target_id: str):
    """A small '?' badge revealing `text` on hover — for tucking away non-critical explanations."""
    return html.Span(
        [
            dbc.Badge("?", id=target_id, color="light", text_color="secondary", pill=True, className="ms-1 border", style={"cursor": "help"}),
            dbc.Tooltip(text, target=target_id),
        ],
        className="d-inline-block",
    )


def with_help(heading, help_text: str, target_id: str, className: str = "mt-3"):
    """A heading/label with a '?' help icon beside it; `help_text` shows on hover."""
    return html.Div(
        [heading, help_icon(help_text, target_id)],
        className=f"d-flex align-items-center {className} mb-1",
    )


def workflow_summary(cfg) -> str:
    """The 'who does what' subtitle under the project name. The two screening stages collapse into
    one term when they match, so the common case still reads as a single line."""
    abstract = cfg.screening_workflow("abstract")
    full_text = cfg.screening_workflow("full_text")
    screening = abstract if abstract == full_text else f"abstract {abstract}, full text {full_text}"
    return f"{cfg.project.type} • screening: {screening} • extraction: {cfg.extraction.workflow}"


_PROMPT_MODE_OPTIONS = [
    {"label": "Plain text", "value": "plain"},
    {"label": "Rendered", "value": "md"},
]


def prompt_view_toggle(radio_id: str):
    """Plain / Rendered switch for a composed-prompt preview; pair with render_prompt_body."""
    return dbc.RadioItems(id=radio_id, options=_PROMPT_MODE_OPTIONS, value="plain", inline=True, className="small mb-1")


def render_prompt_body(text: str, mode: str, *, font: float = 0.95):
    """Render a composed prompt as plain html.Pre or rendered dcc.Markdown per the toggle mode."""
    box = {"border": "1px solid #eee", "borderRadius": "6px", "padding": "8px"}
    if mode == "md":
        return dcc.Markdown(text, style={**box, "fontSize": f"{font}rem"})
    return html.Pre(text, style={"whiteSpace": "pre-wrap", "fontSize": f"{font}rem", **box})


def triggered_click_id() -> Optional[dict]:
    """The pattern-matching id of the input that actually carries a click this cycle.

    Prefer this over ctx.triggered_id for actions on a re-rendering list of cards: ctx.triggered_id
    is the *first* triggered input, which can be a value-less freshly-rendered button, so a click
    that coincides with a re-render would act on the wrong row. This returns the id of the component
    whose value is set (the real click), or None if no real click happened.
    """
    clicked = next(
        (c for c in (ctx.triggered or [])
         if c.get("value") and c["prop_id"].startswith("{")),  # pattern-matching ids only
        None,
    )
    if not clicked:
        return None
    return json.loads(clicked["prop_id"].rsplit(".", 1)[0])


def format_authors(raw: object, limit: int = 3) -> str:
    """Render a sources.authors value (JSON list or list) as 'A; B; C et al.'."""
    if not raw:
        return ""
    authors = raw
    if isinstance(raw, str):
        try:
            authors = json.loads(raw)
        except (ValueError, TypeError):
            return raw
    if not isinstance(authors, list):
        return str(authors)
    shown = "; ".join(str(a) for a in authors[:limit])
    return f"{shown} et al." if len(authors) > limit else shown


def criterion_names() -> dict:
    """Map criterion id -> name for the current project's structured criteria (flag-check display)."""
    try:
        return {c.id: c.name for c in read_criteria_set().criteria if c.id}
    except Exception:
        return {}


_FLAG_VERDICT_COLORS = {"PASS": "success", "FAIL": "danger", "UNCERTAIN": "warning"}


def flag_check_block(flag_check, *, header: bool = True):
    """Per-criterion PASS/FAIL/UNCERTAIN verdicts (badge + criterion name + confidence + reason + quote).
    Shared by the screening/extraction quick tests and the conflict review cards."""
    if not flag_check:
        return None
    names = criterion_names()
    rows = []
    for it in flag_check:
        verdict = (it.get("verdict") or "").upper()
        conf = it.get("confidence")
        cid = it.get("criterion_id") or ""
        quote = it.get("quote")
        rows.append(html.Div([
            html.Div([
                dbc.Badge(verdict or "?", color=_FLAG_VERDICT_COLORS.get(verdict, "secondary"), className="me-2"),
                html.Span(names.get(cid, cid), className="fw-bold small me-2"),
                html.Span(f"conf {conf}" if conf is not None else "", className="text-muted small"),
            ]),
            html.Div(it.get("reason") or "", className="small"),
            html.Div(f"“{quote}”", className="text-muted small fst-italic") if quote else None,
        ], className="mb-2"))
    if header:
        return html.Div([html.Hr(className="my-2"), html.Div("Inclusion flag check", className="fw-bold small mb-1"), *rows], className="mt-1")
    return html.Div(rows, className="mt-1")


def _short_author_year(src) -> str:
    if not src.authors:
        return f"({src.year})" if src.year else ""
    first = src.authors[0].split(",")[0].strip()
    return f"{first} {src.year}" if src.year else first
