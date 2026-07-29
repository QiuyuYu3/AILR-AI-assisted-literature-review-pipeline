"""Regression tests for screening bugs recorded in the CHANGELOG:
- 0.24: batch inserts are chunked (bind-parameter limit)
- 0.17: summary counts only the latest decision per (source, reviewer)
- 0.16.1: reset removes only the human's vote (AI verdict kept) and clears the
  reconciliation so a new differing vote re-enters Conflicts
"""

from ailr.core.source import Source
from ailr.reviewers import ScreeningDecision


def _add_source(project, title="Paper"):
    return project.db.insert_source(Source(title=title, project_id=project.project_id))


def _decision(sid, decision, reviewer_id, reviewer_type="human", stage="abstract"):
    return ScreeningDecision(
        decision=decision, reasoning="test", reviewer_type=reviewer_type,
        reviewer_id=reviewer_id, source_id=sid, stage=stage,
    )


class TestBatchInsertChunking:
    def test_all_rows_land_across_chunks(self, tmp_project):
        db = tmp_project.db
        sids = [_add_source(tmp_project, title=f"P{i}") for i in range(7)]
        decisions = [_decision(sid, "include", "mock:ai", reviewer_type="ai") for sid in sids]
        db.insert_screening_decisions_batch(decisions, chunk=3)  # 3 + 3 + 1
        assert db.count_screening_decisions(tmp_project.project_id, reviewer_type="ai") == 7

    def test_empty_and_missing_source_id_are_skipped(self, tmp_project):
        db = tmp_project.db
        db.insert_screening_decisions_batch([])
        db.insert_screening_decisions_batch([_decision(None, "include", "mock:ai", reviewer_type="ai")])
        assert db.count_screening_decisions(tmp_project.project_id) == 0


class TestSummaryCountsLatestOnly:
    def test_superseded_revote_not_counted(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        db.insert_screening_decision(_decision(sid, "include", "amber"))
        db.insert_screening_decision(_decision(sid, "exclude", "amber"))  # re-vote supersedes
        summary = db.screening_summary(tmp_project.project_id, reviewer_type="human", stage="abstract")
        assert summary == {"include": 0, "exclude": 1, "uncertain": 0}

    def test_each_reviewer_counted_once(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        db.insert_screening_decision(_decision(sid, "include", "amber"))
        db.insert_screening_decision(_decision(sid, "include", "bob"))
        summary = db.screening_summary(tmp_project.project_id, reviewer_type="human", stage="abstract")
        assert summary["include"] == 2

    def test_stages_do_not_leak_into_each_other(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        db.insert_screening_decision(_decision(sid, "include", "amber", stage="abstract"))
        db.insert_screening_decision(_decision(sid, "exclude", "amber", stage="full_text"))
        abstract = db.screening_summary(tmp_project.project_id, reviewer_type="human", stage="abstract")
        full_text = db.screening_summary(tmp_project.project_id, reviewer_type="human", stage="full_text")
        assert abstract == {"include": 1, "exclude": 0, "uncertain": 0}
        assert full_text == {"include": 0, "exclude": 1, "uncertain": 0}


class TestLatestAiDecision:
    def test_rerun_supersedes(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        db.insert_screening_decision(_decision(sid, "exclude", "gpt", reviewer_type="ai"))
        db.insert_screening_decision(_decision(sid, "include", "gpt", reviewer_type="ai"))
        latest = db.get_latest_ai_decision(sid, stage="abstract")
        assert latest["decision"] == "include"
        assert db.get_latest_ai_decisions([sid], stage="abstract") == {sid: "include"}


class TestResetSemantics:
    def test_reset_keeps_the_ai_verdict(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        db.insert_screening_decision(_decision(sid, "exclude", "gpt", reviewer_type="ai"))
        db.insert_screening_decision(_decision(sid, "include", "amber"))
        db.delete_screening_decision(sid, "amber", reviewer_type="human")
        assert db.get_human_decisions(sid, "abstract") == []
        assert db.get_latest_ai_decision(sid, stage="abstract")["decision"] == "exclude"

    def test_stale_reconciliation_does_not_hide_a_new_conflict(self, tmp_project):
        """0.16.1: reset clears the reconciliation, so a fresh differing vote re-enters
        Conflicts instead of being hidden forever by the old final decision."""
        db = tmp_project.db
        pid = tmp_project.project_id
        sid = _add_source(tmp_project)
        db.insert_screening_decision(_decision(sid, "include", "amber"))
        db.insert_screening_decision(_decision(sid, "exclude", "bob"))
        db.insert_screening_reconciliation(sid, "include", adjudicator="pi", stage="abstract")
        assert db.list_screening_conflicts(pid) == []
        # amber resets: her vote AND the reconciliation go (what the reset callback does)
        db.delete_screening_decision(sid, "amber", reviewer_type="human")
        db.delete_reconciliations_for_source(sid, "abstract_screening")
        # she re-votes, still disagreeing with bob -> must re-enter Conflicts
        db.insert_screening_decision(_decision(sid, "uncertain", "amber"))
        assert [s.id for s in db.list_screening_conflicts(pid)] == [sid]
