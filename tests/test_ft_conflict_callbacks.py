"""Full-text vote and conflict-adjudication actions (the extracted callback bodies in
ailr.ui._actions, as used by the Full-text and Conflicts/FT-Conflicts tabs).

The abstract-stage variants of _apply_vote/_apply_reset are covered in
test_screen_callbacks.py; here: stage='full_text' semantics + resolve/undo.
"""

from dash import no_update

from ailr.core.source import Source
from ailr.reviewers import ScreeningDecision
from ailr.ui._actions import _apply_reset, _apply_resolve, _apply_undo_resolve, _apply_vote


def _add_source(project, title="Paper"):
    return project.db.insert_source(Source(title=title, project_id=project.project_id))


def _vote(db, sid, decision, reviewer_id, reviewer_type="human", stage="full_text"):
    db.insert_screening_decision(ScreeningDecision(
        decision=decision, reasoning="test", reviewer_type=reviewer_type,
        reviewer_id=reviewer_id, source_id=sid, stage=stage,
    ))


class TestFullTextVote:
    def test_vote_lands_on_full_text_stage(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _, last = _apply_vote(db, sid, "include", "amber", "assisted", stage="full_text")
        assert last["decision"] == "include"
        assert [d["decision"] for d in db.get_human_decisions(sid, "full_text")] == ["include"]
        assert db.get_human_decisions(sid, "abstract") == []

    def test_stages_lock_independently(self, tmp_project):
        """An abstract vote must not lock the full-text stage (and vice versa)."""
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _apply_vote(db, sid, "include", "amber", "assisted", stage="abstract")
        _, last = _apply_vote(db, sid, "exclude", "amber", "assisted", stage="full_text")
        assert last["decision"] == "exclude"  # not skipped as a double-click

    def test_assisted_blocks_second_human_at_full_text(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _apply_vote(db, sid, "include", "amber", "assisted", stage="full_text")
        _, last = _apply_vote(db, sid, "exclude", "bob", "assisted", stage="full_text")
        assert last["blocked"] is True and last["by"] == "amber"
        assert [d["reviewer_id"] for d in db.get_human_decisions(sid, "full_text")] == ["amber"]

    def test_custom_reasoning_is_stored(self, tmp_project):
        """The exclude-with-reasons modal passes its reasons through the same vote path."""
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _apply_vote(db, sid, "exclude", "amber", "assisted", stage="full_text",
                    reasoning="wrong population; no dyadic interaction")
        [row] = db.get_human_decisions(sid, "full_text")
        assert row["reasoning"] == "wrong population; no dyadic interaction"

    def test_modal_exclude_respects_the_vote_lock(self, tmp_project):
        """Voting include inline, then excluding via the modal, must not stack a second vote."""
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _apply_vote(db, sid, "include", "amber", "assisted", stage="full_text")
        _, last = _apply_vote(db, sid, "exclude", "amber", "assisted", stage="full_text",
                              reasoning="changed my mind")
        assert last is no_update  # skipped: reset first, then re-vote
        assert [d["decision"] for d in db.get_human_decisions(sid, "full_text")] == ["include"]

    def test_independent_modal_exclude_capped_at_two_humans(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _apply_vote(db, sid, "include", "amber", "independent", stage="full_text")
        _apply_vote(db, sid, "include", "bob", "independent", stage="full_text")
        _, last = _apply_vote(db, sid, "exclude", "carol", "independent", stage="full_text",
                              reasoning="via modal")
        assert last["blocked"] is True
        assert {d["reviewer_id"] for d in db.get_human_decisions(sid, "full_text")} == {"amber", "bob"}

    def test_ft_reset_touches_only_full_text(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _apply_vote(db, sid, "include", "amber", "assisted", stage="abstract")
        _apply_vote(db, sid, "exclude", "amber", "assisted", stage="full_text")
        db.insert_screening_reconciliation(sid, "exclude", adjudicator="amber", stage="full_text")
        db.insert_screening_reconciliation(sid, "include", adjudicator="amber", stage="abstract")
        refresh, last = _apply_reset(db, sid, "amber", stage="full_text")
        assert refresh and last is None
        assert db.get_human_decisions(sid, "full_text") == []
        # abstract vote and abstract reconciliation survive
        assert [d["decision"] for d in db.get_human_decisions(sid, "abstract")] == ["include"]
        recs = db.list_reconciliations(tmp_project.project_id, stage="abstract_screening")
        assert len(recs) == 1
        assert db.list_reconciliations(tmp_project.project_id, stage="full_text_screening") == []


class TestResolveConflict:
    def test_resolve_records_final_decision_and_action(self, tmp_project):
        db = tmp_project.db
        pid = tmp_project.project_id
        sid = _add_source(tmp_project)
        _vote(db, sid, "include", "gpt", reviewer_type="ai", stage="abstract")
        _vote(db, sid, "exclude", "amber", stage="abstract")
        assert db.unresolved_conflict_ids(pid, "assisted", stage="abstract") == {sid}
        refresh = _apply_resolve(db, sid, "exclude", "amber", "clearly off-topic", stage="abstract")
        assert refresh and "ts" in refresh
        assert db.unresolved_conflict_ids(pid, "assisted", stage="abstract") == set()
        [rec] = db.list_reconciliations(pid, stage="abstract_screening")
        assert rec["final_value"] == "exclude" and rec["adjudicator"] == "amber"
        assert rec["rationale"] == "clearly off-topic"
        actions = db.get_screening_actions(sid)
        assert ("reconcile", "exclude") in [(a["action"], a["decision"]) for a in actions]

    def test_resolve_full_text_stage_uses_its_own_reconcile_stage(self, tmp_project):
        db = tmp_project.db
        pid = tmp_project.project_id
        sid = _add_source(tmp_project)
        _apply_resolve(db, sid, "include", "amber", None, stage="full_text")
        assert len(db.list_reconciliations(pid, stage="full_text_screening")) == 1
        assert db.list_reconciliations(pid, stage="abstract_screening") == []

    def test_undo_reopens_the_conflict(self, tmp_project):
        db = tmp_project.db
        pid = tmp_project.project_id
        sid = _add_source(tmp_project)
        _vote(db, sid, "include", "gpt", reviewer_type="ai", stage="abstract")
        _vote(db, sid, "exclude", "amber", stage="abstract")
        _apply_resolve(db, sid, "include", "amber", None, stage="abstract")
        [rec] = db.list_reconciliations(pid, stage="abstract_screening")
        refresh = _apply_undo_resolve(db, rec["id"])
        assert refresh and "ts" in refresh
        assert db.list_reconciliations(pid, stage="abstract_screening") == []
        assert db.unresolved_conflict_ids(pid, "assisted", stage="abstract") == {sid}
        actions = [a["action"] for a in db.get_screening_actions(sid)]
        assert "reconcile_undo" in actions

    def test_the_rationale_reaches_the_audit_row(self, tmp_project):
        """History reads screening_actions, not reconciliations: an undo deletes the latter, and a
        paper adjudicated twice keeps only its newest row, so the reason has to live on the event."""
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _apply_resolve(db, sid, "include", "amber", "borderline on the dyadic criterion", stage="abstract")
        [row] = [a for a in db.get_screening_actions(sid) if a["action"] == "reconcile"]
        assert row["rationale"] == "borderline on the dyadic criterion"

    def test_a_rationale_outlives_the_undo_that_removes_the_reconciliation(self, tmp_project):
        db = tmp_project.db
        pid = tmp_project.project_id
        sid = _add_source(tmp_project)
        _apply_resolve(db, sid, "exclude", "amber", "conference abstract only", stage="abstract")
        [rec] = db.list_reconciliations(pid, stage="abstract_screening")
        _apply_undo_resolve(db, rec["id"])

        assert db.list_reconciliations(pid, stage="abstract_screening") == []
        [row] = [a for a in db.get_screening_actions(sid) if a["action"] == "reconcile"]
        assert row["rationale"] == "conference abstract only"

    def test_a_vote_without_reasons_records_no_rationale(self, tmp_project):
        """The stage placeholders ('(inline screening)') are not reasons and must not surface."""
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _apply_vote(db, sid, "include", "amber", "assisted", stage="full_text")
        [row] = [a for a in db.get_screening_actions(sid) if a["action"] == "vote"]
        assert row["rationale"] is None

    def test_a_modal_exclude_carries_its_reasons_into_history(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _apply_vote(db, sid, "exclude", "amber", "assisted", stage="full_text",
                    reasoning="Wrong population; No full text")
        [row] = [a for a in db.get_screening_actions(sid) if a["action"] == "vote"]
        assert row["rationale"] == "Wrong population; No full text"

    def test_undo_missing_reconciliation_is_harmless(self, tmp_project):
        """Harmless means the stale button does nothing, not merely that it does not raise: a
        neighbouring reconciliation must survive and no undo may be logged against it."""
        db = tmp_project.db
        pid = tmp_project.project_id
        sid = _add_source(tmp_project)
        _vote(db, sid, "include", "gpt", reviewer_type="ai", stage="abstract")
        _vote(db, sid, "exclude", "amber", stage="abstract")
        _apply_resolve(db, sid, "include", "amber", None, stage="abstract")

        refresh = _apply_undo_resolve(db, 99999)

        assert refresh and "ts" in refresh
        assert len(db.list_reconciliations(pid, stage="abstract_screening")) == 1
        assert db.unresolved_conflict_ids(pid, "assisted", stage="abstract") == set()
        assert "reconcile_undo" not in [a["action"] for a in db.get_screening_actions(sid)]


class TestAdjudicationVisibility:
    """The review queues blind you to other reviewers' work, but an adjudication is the team's
    conclusion rather than a vote — hiding it left the reason for a paper's fate visible only to
    whoever happened to write it."""

    def test_another_reviewers_adjudication_is_visible_but_their_votes_are_not(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _apply_vote(db, sid, "include", "amber", "independent", stage="abstract")
        _apply_vote(db, sid, "exclude", "bo", "independent", stage="abstract")
        _apply_resolve(db, sid, "exclude", "bo", "off-topic", stage="abstract")

        mine = db.get_screening_actions(sid, reviewer_id="amber")
        kinds = [(a["action"], a["reviewer_id"]) for a in mine]
        assert ("vote", "amber") in kinds
        assert ("vote", "bo") not in kinds
        assert ("reconcile", "bo") in kinds
        assert [a["rationale"] for a in mine if a["action"] == "reconcile"] == ["off-topic"]

    def test_the_undo_of_another_reviewers_adjudication_is_visible_too(self, tmp_project):
        """Without it a withdrawn ruling would still read as the current final decision."""
        db = tmp_project.db
        pid = tmp_project.project_id
        sid = _add_source(tmp_project)
        _apply_vote(db, sid, "include", "amber", "independent", stage="abstract")
        _apply_resolve(db, sid, "exclude", "bo", None, stage="abstract")
        [rec] = db.list_reconciliations(pid, stage="abstract_screening")
        _apply_undo_resolve(db, rec["id"])

        actions = [a["action"] for a in db.get_screening_actions(sid, reviewer_id="amber")]
        assert "reconcile" in actions and "reconcile_undo" in actions

    def test_the_all_reviewer_view_is_unchanged(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _apply_vote(db, sid, "include", "amber", "independent", stage="abstract")
        _apply_vote(db, sid, "exclude", "bo", "independent", stage="abstract")
        assert len(db.get_screening_actions(sid)) == 2


class TestFullTextExcludeKeepsPrismaReasonsClean:
    """A full-text Exclude adjudication doubles as the PRISMA exclusion reason, and
    full_text_exclusion_counts only splits a ';' list when every part is a defined reason. Free
    text merged into it would turn one report into a reason category of its own."""

    def _conflicted(self, project, reviewer_b="bo"):
        db = project.db
        sid = _add_source(project)
        _apply_vote(db, sid, "include", "amber", "independent", stage="full_text")
        _apply_vote(db, sid, "exclude", reviewer_b, "independent", stage="full_text")
        return sid

    def test_a_typed_note_reaches_history_without_polluting_the_reasons(self, tmp_project):
        db = tmp_project.db
        pid = tmp_project.project_id
        for name in ("Wrong population", "No full text"):
            db.create_exclusion_reason(pid, name)
        sid = self._conflicted(tmp_project)

        _apply_resolve(
            db, sid, "exclude", "bo", "Wrong population; No full text", stage="full_text",
            audit_rationale="Wrong population; No full text — only a conference abstract",
        )

        counts = {r["reason"]: r["n"] for r in db.full_text_exclusion_counts(pid)}
        assert counts == {"Wrong population": 1, "No full text": 1}
        [row] = [a for a in db.get_screening_actions(sid) if a["action"] == "reconcile"]
        assert row["rationale"] == "Wrong population; No full text — only a conference abstract"

    def test_without_a_note_the_audit_row_keeps_the_reasons(self, tmp_project):
        db = tmp_project.db
        pid = tmp_project.project_id
        db.create_exclusion_reason(pid, "Wrong population")
        sid = self._conflicted(tmp_project)

        _apply_resolve(db, sid, "exclude", "bo", "Wrong population", stage="full_text")

        [row] = [a for a in db.get_screening_actions(sid) if a["action"] == "reconcile"]
        assert row["rationale"] == "Wrong population"
        assert {r["reason"] for r in db.full_text_exclusion_counts(pid)} == {"Wrong population"}
