"""Full-text vote and conflict-adjudication actions (the extracted callback bodies in
ailr.ui._actions, as used by the Full-text and Conflicts/FT-Conflicts tabs).

The abstract-stage variants of _apply_vote/_apply_reset are covered in
test_screen_callbacks.py; here: stage='full_text' semantics + resolve/undo.
"""

import pytest
from dash import no_update

from ailr.ui import full_text_view
from ailr.ui._actions import _apply_reset, _apply_resolve, _apply_undo_resolve, _apply_vote
from ailr.ui._conflicts_base import initial_payload
from ailr.ui.conflicts_view import _CFG as ABSTRACT_CONFLICTS
from tests.helpers import add_source, callbacks_of, vote, walk


class TestFullTextVote:
    def test_vote_lands_on_full_text_stage(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        _, last = _apply_vote(db, sid, "include", "amber", "assisted", stage="full_text")
        assert last["decision"] == "include"
        assert [d["decision"] for d in db.get_human_decisions(sid, "full_text")] == ["include"]
        assert db.get_human_decisions(sid, "abstract") == []

    def test_stages_lock_independently(self, tmp_project):
        """An abstract vote must not lock the full-text stage (and vice versa)."""
        db = tmp_project.db
        sid = add_source(tmp_project)
        _apply_vote(db, sid, "include", "amber", "assisted", stage="abstract")
        _, last = _apply_vote(db, sid, "exclude", "amber", "assisted", stage="full_text")
        assert last["decision"] == "exclude"  # not skipped as a double-click

    def test_assisted_blocks_second_human_at_full_text(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        _apply_vote(db, sid, "include", "amber", "assisted", stage="full_text")
        _, last = _apply_vote(db, sid, "exclude", "bob", "assisted", stage="full_text")
        assert last["blocked"] is True and last["by"] == "amber"
        assert [d["reviewer_id"] for d in db.get_human_decisions(sid, "full_text")] == ["amber"]

    def test_custom_reasoning_is_stored(self, tmp_project):
        """The exclude-with-reasons modal passes its reasons through the same vote path."""
        db = tmp_project.db
        sid = add_source(tmp_project)
        _apply_vote(db, sid, "exclude", "amber", "assisted", stage="full_text",
                    reasoning="wrong population; no dyadic interaction")
        [row] = db.get_human_decisions(sid, "full_text")
        assert row["reasoning"] == "wrong population; no dyadic interaction"

    def test_modal_exclude_respects_the_vote_lock(self, tmp_project):
        """Voting include inline, then excluding via the modal, must not stack a second vote."""
        db = tmp_project.db
        sid = add_source(tmp_project)
        _apply_vote(db, sid, "include", "amber", "assisted", stage="full_text")
        _, last = _apply_vote(db, sid, "exclude", "amber", "assisted", stage="full_text",
                              reasoning="changed my mind")
        assert last is no_update  # skipped: reset first, then re-vote
        assert [d["decision"] for d in db.get_human_decisions(sid, "full_text")] == ["include"]

    def test_independent_modal_exclude_capped_at_two_humans(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        _apply_vote(db, sid, "include", "amber", "independent", stage="full_text")
        _apply_vote(db, sid, "include", "bob", "independent", stage="full_text")
        _, last = _apply_vote(db, sid, "exclude", "carol", "independent", stage="full_text",
                              reasoning="via modal")
        assert last["blocked"] is True
        assert {d["reviewer_id"] for d in db.get_human_decisions(sid, "full_text")} == {"amber", "bob"}

    def test_ft_reset_touches_only_full_text(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
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
        sid = add_source(tmp_project)
        vote(db, sid, "include", "gpt", stage="abstract", reviewer_type="ai")
        vote(db, sid, "exclude", "amber", stage="abstract")
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
        sid = add_source(tmp_project)
        _apply_resolve(db, sid, "include", "amber", None, stage="full_text")
        assert len(db.list_reconciliations(pid, stage="full_text_screening")) == 1
        assert db.list_reconciliations(pid, stage="abstract_screening") == []

    def test_undo_reopens_the_conflict(self, tmp_project):
        db = tmp_project.db
        pid = tmp_project.project_id
        sid = add_source(tmp_project)
        vote(db, sid, "include", "gpt", stage="abstract", reviewer_type="ai")
        vote(db, sid, "exclude", "amber", stage="abstract")
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
        sid = add_source(tmp_project)
        _apply_resolve(db, sid, "include", "amber", "borderline on the dyadic criterion", stage="abstract")
        [row] = [a for a in db.get_screening_actions(sid) if a["action"] == "reconcile"]
        assert row["rationale"] == "borderline on the dyadic criterion"

    def test_a_rationale_outlives_the_undo_that_removes_the_reconciliation(self, tmp_project):
        db = tmp_project.db
        pid = tmp_project.project_id
        sid = add_source(tmp_project)
        _apply_resolve(db, sid, "exclude", "amber", "conference abstract only", stage="abstract")
        [rec] = db.list_reconciliations(pid, stage="abstract_screening")
        _apply_undo_resolve(db, rec["id"])

        assert db.list_reconciliations(pid, stage="abstract_screening") == []
        [row] = [a for a in db.get_screening_actions(sid) if a["action"] == "reconcile"]
        assert row["rationale"] == "conference abstract only"

    def test_a_vote_without_reasons_records_no_rationale(self, tmp_project):
        """The stage placeholders ('(inline screening)') are not reasons and must not surface."""
        db = tmp_project.db
        sid = add_source(tmp_project)
        _apply_vote(db, sid, "include", "amber", "assisted", stage="full_text")
        [row] = [a for a in db.get_screening_actions(sid) if a["action"] == "vote"]
        assert row["rationale"] is None

    def test_a_modal_exclude_carries_its_reasons_into_history(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        _apply_vote(db, sid, "exclude", "amber", "assisted", stage="full_text",
                    reasoning="Wrong population; No full text")
        [row] = [a for a in db.get_screening_actions(sid) if a["action"] == "vote"]
        assert row["rationale"] == "Wrong population; No full text"

    def test_a_failed_audit_row_rolls_the_whole_undo_back(self, tmp_project, monkeypatch):
        """The two writes are one transaction: a failure on the second must not leave the
        reconciliation deleted with nothing in History to say who deleted it."""
        db = tmp_project.db
        pid = tmp_project.project_id
        sid = add_source(tmp_project)
        _apply_resolve(db, sid, "include", "amber", None, stage="abstract")
        [rec] = db.list_reconciliations(pid, stage="abstract_screening")

        def boom(*a, **k):
            raise RuntimeError("write failed")

        monkeypatch.setattr(db, "insert_screening_action", boom)
        with pytest.raises(RuntimeError):
            _apply_undo_resolve(db, rec["id"])

        assert len(db.list_reconciliations(pid, stage="abstract_screening")) == 1

    def test_undo_missing_reconciliation_is_harmless(self, tmp_project):
        """Harmless means the stale button does nothing, not merely that it does not raise: a
        neighbouring reconciliation must survive and no undo may be logged against it."""
        db = tmp_project.db
        pid = tmp_project.project_id
        sid = add_source(tmp_project)
        vote(db, sid, "include", "gpt", stage="abstract", reviewer_type="ai")
        vote(db, sid, "exclude", "amber", stage="abstract")
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
        sid = add_source(tmp_project)
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
        sid = add_source(tmp_project)
        _apply_vote(db, sid, "include", "amber", "independent", stage="abstract")
        _apply_resolve(db, sid, "exclude", "bo", None, stage="abstract")
        [rec] = db.list_reconciliations(pid, stage="abstract_screening")
        _apply_undo_resolve(db, rec["id"])

        actions = [a["action"] for a in db.get_screening_actions(sid, reviewer_id="amber")]
        assert "reconcile" in actions and "reconcile_undo" in actions

    def test_the_all_reviewer_view_is_unchanged(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        _apply_vote(db, sid, "include", "amber", "independent", stage="abstract")
        _apply_vote(db, sid, "exclude", "bo", "independent", stage="abstract")
        assert len(db.get_screening_actions(sid)) == 2


class TestFullTextExcludeKeepsPrismaReasonsClean:
    """A full-text Exclude adjudication doubles as the PRISMA exclusion reason, and
    full_text_exclusion_counts only splits a ';' list when every part is a defined reason. Free
    text merged into it would turn one report into a reason category of its own."""

    def _conflicted(self, project, reviewer_b="bo"):
        db = project.db
        sid = add_source(project)
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

        counts = {r["reason"]: r["n"] for r in db.full_text_exclusion_counts(pid, workflow="independent")}
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
        assert {r["reason"] for r in db.full_text_exclusion_counts(pid, workflow="independent")} == {"Wrong population"}


class TestRecentlyResolved:
    def test_undo_is_offered_on_the_newest_rulings(self, tmp_project):
        """The list is the only place an adjudication can be undone, so it must keep the newest."""
        db = tmp_project.db
        for i in range(11):
            db.insert_screening_reconciliation(add_source(tmp_project, f"P{i}"), "include", "pi", "", stage="abstract")
        ids = sorted(r["id"] for r in db.list_reconciliations(tmp_project.project_id, "abstract_screening", limit=100))
        _cards, _count, resolved = initial_payload(ABSTRACT_CONFLICTS)
        offered = {n.id["rec_id"] for n in walk(resolved)
                   if isinstance(getattr(n, "id", None), dict) and n.id.get("type") == "conflict-undo"}
        assert offered == set(ids[-10:])


class TestFullTextPageCallbacks:
    """The full-text tab's registered callbacks, driven as a click would drive them."""

    @pytest.fixture
    def fns(self):
        return callbacks_of(full_text_view)

    def test_a_vote_button_records_a_full_text_vote(self, tmp_project, fns, click):
        sid = add_source(tmp_project)
        click({"type": "ft-decide", "source": sid, "decision": "exclude"})
        fns["_on_action"]([1], [], [], "amber")
        assert [d["decision"] for d in tmp_project.db.get_human_decisions(sid, "full_text")] == ["exclude"]
        assert tmp_project.db.get_human_decisions(sid, "abstract") == []

    def test_reset_withdraws_only_the_full_text_vote(self, tmp_project, fns, click):
        sid = add_source(tmp_project)
        vote(tmp_project.db, sid, "include", "amber", stage="abstract")
        vote(tmp_project.db, sid, "include", "amber", stage="full_text")
        click({"type": "ft-reset", "source": sid})
        fns["_on_action"]([], [1], [], "amber")
        assert tmp_project.db.get_human_decisions(sid, "full_text") == []
        assert [d["reviewer_id"] for d in tmp_project.db.get_human_decisions(sid, "abstract")] == ["amber"]

    def test_marking_a_full_text_unobtainable_and_back(self, tmp_project, fns, click):
        sid = add_source(tmp_project)
        for flag, expected in ((1, True), (0, False)):
            click({"type": "ft-retrieval", "source": sid, "flag": flag})
            fns["_on_action"]([], [], [1], "amber")
            assert tmp_project.db.get_source(sid).full_text_not_retrieved is expected

    def test_the_banner_undo_ignores_its_own_re_creation(self, tmp_project, fns):
        sid = add_source(tmp_project)
        vote(tmp_project.db, sid, "include", "amber", stage="full_text")
        assert fns["_undo"](None, {"sid": sid}, "amber") == (no_update, no_update)
        assert len(tmp_project.db.get_human_decisions(sid, "full_text")) == 1
        fns["_undo"](1, {"sid": sid}, "amber")
        assert tmp_project.db.get_human_decisions(sid, "full_text") == []

    def test_marking_a_duplicate_from_the_card(self, tmp_project, fns, click):
        sid = add_source(tmp_project)
        click({"type": "ft-duplicate", "source": sid})
        fns["_on_ft_mark_duplicate"]([1])
        assert [s["id"] for s in tmp_project.db.list_manual_duplicates(tmp_project.project_id)] == [sid]

    def test_the_exclude_modal_records_a_vote_with_its_reasons(self, tmp_project, fns):
        sid = add_source(tmp_project)
        fns["_confirm_exclude"](1, ["Wrong population", "Wrong design"], {"sid": sid, "mode": "vote"}, "amber", [], [])
        [d] = tmp_project.db.get_human_decisions(sid, "full_text")
        assert (d["decision"], d["reasoning"]) == ("exclude", "Wrong population; Wrong design")

    def test_a_ruling_keeps_the_reasons_clean_and_files_the_cards_note_in_history(self, tmp_project, fns):
        db, pid = tmp_project.db, tmp_project.project_id
        for name in ("Wrong population", "No full text"):
            db.create_exclusion_reason(pid, name)
        sid, other = add_source(tmp_project, "ruled"), add_source(tmp_project, "another card")
        vote(db, sid, "include", "amber", stage="full_text")
        vote(db, sid, "exclude", "bo", stage="full_text")

        fns["_confirm_exclude"](
            1, ["Wrong population", "No full text"], {"sid": sid, "mode": "resolve"}, "pi",
            ["someone else's note", "only a conference abstract"],
            [{"type": "ft-conflict-rationale", "source": other}, {"type": "ft-conflict-rationale", "source": sid}],
        )
        [rec] = db.list_reconciliations(pid, "full_text_screening")
        assert (rec["source_id"], rec["final_value"], rec["rationale"]) == (sid, "exclude", "Wrong population; No full text")
        [row] = [a for a in db.get_screening_actions(sid) if a["action"] == "reconcile"]
        assert row["rationale"].startswith("Wrong population; No full text") and row["rationale"].endswith("only a conference abstract")
        assert "someone else" not in row["rationale"]
        counts = {r["reason"]: r["n"] for r in db.full_text_exclusion_counts(pid, workflow="independent")}
        assert counts == {"Wrong population": 1, "No full text": 1}

    @pytest.mark.parametrize("reviewer,reasons,message", [
        ("  ", ["Wrong population"], "Enter your reviewer ID first."),
        ("amber", [], "Pick or add at least one reason."),
    ])
    def test_the_exclude_modal_refuses_without_a_reviewer_or_a_reason(self, tmp_project, fns, reviewer, reasons, message):
        sid = add_source(tmp_project)
        out = fns["_confirm_exclude"](1, reasons, {"sid": sid, "mode": "vote"}, reviewer, [], [])
        assert message in str(out[3].children)
        assert tmp_project.db.get_human_decisions(sid, "full_text") == []
