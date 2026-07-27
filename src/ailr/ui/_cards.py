"""Card parts shared by the Screening, Full-text, and Conflicts lists.

Deliberately small pure functions rather than one configurable card: the screening and full-text
cards agree on the header, metadata, chips, and decision controls, but each carries its own set of
extras (study grouping and retrieval on one side, abstract collapse on the other). Folding those
into a single builder costs more than it saves — see _conflicts_base for the case where a merged
implementation *was* worth it.
"""

from typing import Any, Optional

import dash_bootstrap_components as dbc
from dash import html

from ailr.ui._common import _short_author_year


DECISION_COLORS = {"include": "success", "exclude": "danger", "uncertain": "warning"}


def header_line(src: Any, badges: Optional[list] = None) -> Any:
    """'#12  Smith 2020' plus any status badges that belong on the same line."""
    return html.Div(
        [
            html.Strong(f"#{src.id}  ", className="text-muted"),
            html.Span(_short_author_year(src), className="text-muted me-2"),
            *(badges or []),
        ]
    )


def meta_line(src: Any, include_database: bool = False) -> Any:
    parts: list[str] = []
    if src.journal:
        parts.append(src.journal)
    if src.year:
        parts.append(str(src.year))
    if include_database and src.source_database:
        parts.append(f"[{src.source_database}]")
    return html.P(" • ".join(parts), className="text-muted small mb-1")


def doi_link(src: Any) -> Any:
    if not src.doi:
        return None
    return html.Div(
        html.A(f"DOI: {src.doi}", href=f"https://doi.org/{src.doi}", target="_blank", className="small")
    )


def tag_chips(tags: Optional[list[dict]]) -> Any:
    if not tags:
        return html.Span()
    return html.Div(
        [dbc.Badge(t["name"], color=t.get("color") or "secondary", pill=True, className="me-1") for t in tags],
        className="mt-1 mb-1",
    )


def peer_note(workflow: str, peer_count: int) -> Any:
    """'N other reviewer(s) voted' — only meaningful when two humans share the stage."""
    if workflow != "independent" or peer_count <= 0:
        return None
    return html.Small(f"{peer_count} other reviewer(s) voted", className="text-muted d-block mt-1")


def decision_controls(sid: Any, my_decision: Optional[str], prefix: str, exclude_id: Any = None) -> list:
    """The include/exclude/uncertain group, or the recorded verdict plus Reset once you have voted.

    `prefix` namespaces the pattern-matching ids ('screen' / 'ft'). `exclude_id` replaces the
    Exclude button's id where excluding goes through a modal first (full text needs a PRISMA reason).
    """
    if my_decision:
        return [
            dbc.Badge(
                my_decision.upper(),
                color=DECISION_COLORS.get(my_decision, "secondary"),
                className="me-2 p-2",
                style={"fontSize": "0.9rem"},
            ),
            dbc.Button(
                "Reset",
                id={"type": f"{prefix}-reset", "source": sid},
                size="sm",
                color="link",
                className="p-0 text-decoration-none",
            ),
        ]

    def _decide(label: str, decision: str, color: str, button_id: Any = None):
        return dbc.Button(
            label,
            id=button_id or {"type": f"{prefix}-decide", "source": sid, "decision": decision},
            color=color,
            size="sm",
        )

    return [
        dbc.ButtonGroup(
            [
                _decide("Include", "include", "success"),
                _decide("Exclude", "exclude", "danger", exclude_id),
                _decide("Uncertain", "uncertain", "warning"),
            ]
        )
    ]


def action_banner(last: Any, undo_id: str, verb: str) -> Any:
    """The 'Saved …  Undo' strip above a decision list, or the warning when the vote lock refused it.

    `verb` is how the stage describes the action already taken ('screened' / 'reviewed').
    """
    if not last or not isinstance(last, dict):
        return ""
    if last.get("blocked"):
        return dbc.Alert(
            [
                html.Span(f"#{last.get('sid')} was already {verb} by ", className="me-1"),
                html.Strong(str(last.get("by", "another reviewer"))),
                html.Span(" — your vote was skipped (assisted mode: one human per paper).", className="ms-1"),
            ],
            color="warning",
            className="py-2 mb-2",
        )
    decision = last.get("decision", "")
    author_year = last.get("author_year", "")
    title = last.get("title", "")
    title_short = (title[:80] + "…") if len(title) > 80 else title
    return dbc.Alert(
        [
            html.Span("Saved ", className="me-1"),
            dbc.Badge(decision.upper(), color=DECISION_COLORS.get(decision, "secondary"), className="me-2"),
            html.Strong(f"#{last.get('sid')} ", className="me-1"),
            html.Span(f"{author_year} ", className="me-2") if author_year else None,
            html.Em(f"“{title_short}”", className="me-3 text-muted small") if title_short else None,
            dbc.Button("Undo", id=undo_id, color="link", size="sm", className="p-0"),
        ],
        color="light",
        className="py-2 mb-2 d-flex align-items-center flex-wrap",
    )
