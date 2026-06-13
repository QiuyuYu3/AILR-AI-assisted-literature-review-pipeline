"""Full-text conflicts: stage='full_text' conflicts between human reviewers."""

from typing import Any

from ailr.ui._conflicts_base import ConflictConfig, build_layout
from ailr.ui._conflicts_base import register_callbacks as _register

_CFG = ConflictConfig(
    prefix="ft-conflict",
    store_prefix="ft-conflicts",
    stage="full_text",
    reconcile_list_stage="full_text_screening",
    title="Resolve full-text conflicts",
    assisted_desc="Assisted mode: AI (flag_check) vs human disagreements at the full-text stage.",
    independent_desc="Independent mode: two human reviewers disagreed at the full-text stage.",
    no_conflicts_msg="No unresolved full-text conflicts. ✓",
    votes_label="Reviewer votes (full-text stage)",
    blank_reasonings=("(full-text review)", "(inline screening)"),
    show_flag_check=True,
    show_abstract_extras=False,
)


def layout() -> Any:
    return build_layout(_CFG)


def register_callbacks(app: Any) -> None:
    _register(app, _CFG)
