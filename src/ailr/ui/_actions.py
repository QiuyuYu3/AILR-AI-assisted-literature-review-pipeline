"""Screening/adjudication actions shared by the Screen, Full-text, and Conflicts tabs.

Module-level (not closures) so the vote lock and reconciliation semantics are directly
testable; each view's callback stays a thin shell: parse the click, call one of these.
"""

import time
from typing import Any, Optional

from dash import no_update

from ailr.core._db_screening import reconcile_stage_for as _reconcile_stage
from ailr.reviewers import ScreeningDecision
from ailr.ui._common import _short_author_year

_VOTE_REASONING = {"abstract": "(inline screening)", "full_text": "(full-text review)"}


def _apply_vote(
    db: Any, source_id: int, decision: str, rid: str, workflow: str,
    stage: str = "abstract", reasoning: Optional[str] = None,
) -> tuple:
    """Record one human vote behind the vote lock. Returns (refresh, last_action) for the callback.
    Lock in one query: skip if I already decided this paper (rapid double-click), and cap the
    team size — 1 human (+ AI) in assisted, 2 humans in independent."""
    i_voted, others = db.screening_lock_check(source_id, rid, stage)
    if i_voted:
        return {"ts": time.time()}, no_update
    team_humans = 1 if workflow == "assisted" else 2
    if others >= team_humans:
        other = db.other_human_decided(source_id, stage, rid) or "another reviewer"
        return {"ts": time.time()}, {"blocked": True, "by": other, "sid": source_id, "ts": time.time()}
    with db._conn.transaction():  # decision + action in one commit
        db.insert_screening_decision(
            ScreeningDecision(
                decision=decision,
                reasoning=reasoning or _VOTE_REASONING.get(stage, ""),
                reviewer_type="human",
                reviewer_id=rid,
                source_id=source_id,
                stage=stage,
            )
        )
        db.insert_screening_action(source_id, rid, action="vote", decision=decision)
    src = db.get_source(source_id)
    return {"ts": time.time()}, {
        "sid": source_id,
        "decision": decision,
        "author_year": _short_author_year(src) if src else "",
        "title": src.title if src else "",
        "ts": time.time(),
    }


def _apply_reset(db: Any, source_id: int, rid: str, stage: str = "abstract") -> tuple:
    """Undo my vote and any final decision at this stage so the paper is re-reviewable.
    Returns (refresh, last_action)."""
    db.delete_screening_decision(source_id, rid, stage=stage, reviewer_type="human")
    db.delete_reconciliations_for_source(source_id, _reconcile_stage(stage))
    db.insert_screening_action(source_id, rid, action="reset")
    return {"ts": time.time()}, None  # clear banner


def _apply_resolve(db: Any, source_id: int, decision: str, rid: str, rationale: Optional[str], stage: str) -> dict:
    """Adjudicate a conflict: record the final decision (+ audit row). Returns the refresh payload."""
    db.insert_screening_reconciliation(source_id, decision, rid, rationale, stage=stage)
    db.insert_screening_action(source_id, rid, action="reconcile", decision=decision)
    return {"ts": time.time()}


def _apply_undo_resolve(db: Any, rec_id: int) -> dict:
    """Undo a reconciliation, so the conflict re-enters the queue. Returns the refresh payload."""
    # Fetch the row to learn source_id + adjudicator before deleting (for the audit trail).
    row = db.get_reconciliation(rec_id)
    db.delete_reconciliation(rec_id)
    if row:
        db.insert_screening_action(row["source_id"], row["adjudicator"], action="reconcile_undo")
    return {"ts": time.time()}
