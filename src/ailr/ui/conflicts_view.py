"""Conflicts / Reconciliation tab: human-vs-human disagreements with adjudicator final decisions."""

from typing import Any

from ailr.ui._conflicts_base import ConflictConfig, ai_detail_block, build_layout
from ailr.ui._conflicts_base import register_callbacks as _register

__all__ = ["ai_detail_block", "layout", "register_callbacks"]

_CFG = ConflictConfig(
    prefix="conflict",
    store_prefix="conflicts",
    stage="abstract",
    reconcile_list_stage="abstract_screening",
    title="Resolve conflicts",
    assisted_desc="Assisted mode: sources where the AI and the human reviewer disagreed. Pick the final decision; recorded with you as adjudicator.",
    independent_desc="Independent mode: sources where two human reviewers disagreed. Pick the final decision; recorded with you as adjudicator.",
    no_conflicts_msg="No unresolved conflicts. ✓",
    votes_label="Reviewer votes",
    blank_reasonings=("(inline screening)",),
    show_flag_check=False,
    show_abstract_extras=True,
)


def layout() -> Any:
    return build_layout(_CFG)


def register_callbacks(app: Any) -> None:
    _register(app, _CFG)
