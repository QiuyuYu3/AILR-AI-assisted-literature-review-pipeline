"""Screening/adjudication actions shared by the Screen, Full-text, and Conflicts tabs.

Module-level (not closures) so the vote lock and reconciliation semantics are directly
testable; each view's callback stays a thin shell: parse the click, call one of these.
"""

import time
from typing import Any

from dash import no_update

from ailr.core._db_screening import reconcile_stage_for as _reconcile_stage
from ailr.reviewers import ScreeningDecision
from ailr.ui._common import _short_author_year

_VOTE_REASONING = {"abstract": "(inline screening)", "full_text": "(full-text review)"}


def _apply_vote(
    db: Any, source_id: int, decision: str, rid: str, workflow: str,
    stage: str = "abstract", reasoning: str | None = None,
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
        # Only a real reason reaches the audit row; the stage placeholders above say nothing.
        db.insert_screening_action(source_id, rid, action="vote", decision=decision, rationale=reasoning)
    src = db.get_source(source_id)
    return {"ts": time.time()}, {
        "sid": source_id,
        "rid": rid,
        "decision": decision,
        "author_year": _short_author_year(src) if src else "",
        "title": src.title if src else "",
        "ts": time.time(),
    }


def _apply_reset(db: Any, source_id: int, rid: str, stage: str = "abstract") -> tuple:
    """Undo my vote and any final decision at this stage so the paper is re-reviewable.
    Returns (refresh, last_action)."""
    # One commit: dropping the vote but keeping the reconciliation would leave a final decision
    # standing on no vote at all, and nothing in the UI would show it.
    with db._conn.transaction():
        # Without a vote of mine there is nothing to reset, and the adjudication is someone else's.
        if db.delete_screening_decision(source_id, rid, stage=stage, reviewer_type="human"):
            db.delete_reconciliations_for_source(source_id, _reconcile_stage(stage))
            db.insert_screening_action(source_id, rid, action="reset")
    return {"ts": time.time()}, None  # clear banner


def _apply_resolve(
    db: Any, source_id: int, decision: str, rid: str, rationale: str | None, stage: str,
    audit_rationale: str | None = None, workflow: str | None = None,
) -> dict:
    """Adjudicate a conflict: record the final decision (+ audit row). Returns the refresh payload.

    A full-text exclusion keeps its PRISMA reasons verbatim in `rationale` — full_text_exclusion_counts
    splits that string on ';' and only counts the parts it recognises, so free text appended there
    would turn one report into its own reason category. Any typed note rides on `audit_rationale`,
    which only the History timeline reads.
    """
    with db._conn.transaction():  # decision + audit row in one commit
        # A card left open may show a conflict someone has since resolved or withdrawn.
        if workflow is not None and not _conflict_is_open(db, source_id, workflow, stage):
            return {"ts": time.time()}
        db.insert_screening_reconciliation(source_id, decision, rid, rationale, stage=stage)
        db.insert_screening_action(
            source_id, rid, action="reconcile", decision=decision, rationale=audit_rationale or rationale
        )
    return {"ts": time.time()}


def _apply_undo_resolve(db: Any, rec_id: int, rid: str) -> dict:
    """Undo an adjudication, so the conflict re-enters the queue. Returns the refresh payload."""
    row = db.get_reconciliation(rec_id)
    if row:
        with db._conn.transaction():  # without the audit row the decision would vanish from History
            # The whole stage goes, so an older adjudication of the same paper cannot stand in for it.
            db.delete_reconciliations_for_source(row["source_id"], row["stage"])
            db.insert_screening_action(row["source_id"], rid, action="reconcile_undo")
    return {"ts": time.time()}


def _conflict_is_open(db: Any, source_id: int, workflow: str, stage: str) -> bool:
    src = db.get_source(source_id)
    return src is not None and source_id in db.unresolved_conflict_ids(src.project_id, workflow, stage)
