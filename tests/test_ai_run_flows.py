"""AI screening run orchestration with the mock client (no API):
- only unscreened sources are processed; a second run is a no-op
- no-abstract sources get a placeholder 'uncertain' (so they aren't retried forever)
- batch mode buffers and lands everything
- 'Clear mock AI results' removes ONLY mock rows; a cleared source is re-screenable
- 'AI outdated' (stale) detection compares the decision's composed prompt vs the current one
"""

import json

from ailr.core.source import Source
from ailr.llm.mock import MockLLMClient
from ailr.reviewers import LLMReviewer, ScreeningDecision
from ailr.tasks.screen import ScreeningTask

_INCLUDE_RESPONSE = {
    "decision": "include",
    "reasoning": "mock says fits",
    "matched_criteria": [],
    "evidence_quotes": [],
    "confidence": 8,
}


def _add_source(project, title="Paper", abstract="An abstract."):
    return project.db.insert_source(Source(
        title=title, abstract=abstract, project_id=project.project_id,
    ))


def _mock_reviewer(response=_INCLUDE_RESPONSE):
    return LLMReviewer(MockLLMClient(response=response))


class TestScreeningRun:
    def test_run_screens_all_unscreened(self, tmp_project):
        db = tmp_project.db
        sids = [_add_source(tmp_project, f"P{i}") for i in range(3)]
        summary = ScreeningTask(tmp_project, _mock_reviewer()).run()
        assert summary.total == 3 and summary.screened == 3 and summary.include == 3
        assert summary.failed == 0
        for sid in sids:
            latest = db.get_latest_ai_decision(sid, "abstract")
            assert latest["decision"] == "include"
            assert latest["reviewer_id"] == "mock:mock"

    def test_second_run_skips_already_screened(self, tmp_project):
        _add_source(tmp_project)
        ScreeningTask(tmp_project, _mock_reviewer()).run()
        again = ScreeningTask(tmp_project, _mock_reviewer()).run()
        assert again.total == 0 and again.screened == 0
        assert tmp_project.db.count_screening_decisions(tmp_project.project_id, reviewer_type="ai") == 1

    def test_human_votes_do_not_block_the_ai(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        db.insert_screening_decision(ScreeningDecision(
            decision="exclude", reasoning="t", reviewer_type="human",
            reviewer_id="amber", source_id=sid, stage="abstract",
        ))
        summary = ScreeningTask(tmp_project, _mock_reviewer()).run()
        assert summary.screened == 1  # unscreened is per reviewer_type

    def test_no_abstract_gets_a_placeholder_uncertain(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, "no abstract", abstract=None)
        client = MockLLMClient(response=_INCLUDE_RESPONSE)
        summary = ScreeningTask(tmp_project, LLMReviewer(client)).run()
        assert summary.skipped_no_abstract == 1
        assert client.call_count == 0  # no LLM call for it
        latest = db.get_latest_ai_decision(sid, "abstract")
        assert latest["decision"] == "uncertain" and latest["confidence"] == 1
        # and it is not re-attempted on the next run
        assert ScreeningTask(tmp_project, _mock_reviewer()).run().total == 0

    def test_batch_mode_lands_everything(self, tmp_project):
        [_add_source(tmp_project, f"P{i}") for i in range(4)]
        summary = ScreeningTask(tmp_project, _mock_reviewer()).run(batch=True)
        assert summary.screened == 4
        assert tmp_project.db.count_screening_decisions(tmp_project.project_id, reviewer_type="ai") == 4

    def test_limit_caps_the_run(self, tmp_project):
        [_add_source(tmp_project, f"P{i}") for i in range(3)]
        summary = ScreeningTask(tmp_project, _mock_reviewer()).run(limit=2)
        assert summary.total == 2 and summary.screened == 2

    def test_raw_output_is_stored_as_json(self, tmp_project):
        """0.24 regression: raw_output must be JSON, not a Python repr."""
        db = tmp_project.db
        sid = _add_source(tmp_project)
        ScreeningTask(tmp_project, _mock_reviewer()).run()
        row = db._conn.execute(
            "SELECT raw_output FROM screening_decisions WHERE source_id = ?", (sid,)
        ).fetchone()
        assert json.loads(row["raw_output"])["decision"] == "include"


class TestApiTelemetry:
    """Token rows are buffered and written once at the end of a run instead of one round trip per
    paper. The rows themselves are still per call, and carry no spend estimate.
    """

    def test_one_row_per_call_written_after_the_run(self, tmp_project):
        [_add_source(tmp_project, f"P{i}") for i in range(3)]
        summary = ScreeningTask(tmp_project, _mock_reviewer()).run()

        rows = tmp_project.db.api_call_summary(tmp_project.project_id)
        assert len(rows) == 1                       # one (provider, model) group
        assert rows[0]["calls"] == 3
        assert rows[0]["input_tokens"] == summary.total_input_tokens
        assert rows[0]["output_tokens"] == summary.total_output_tokens

    def test_no_spend_estimate_is_reported(self, tmp_project):
        _add_source(tmp_project)
        ScreeningTask(tmp_project, _mock_reviewer()).run()

        assert "cost_estimate" not in tmp_project.db.api_call_summary(tmp_project.project_id)[0]

    def test_a_run_that_made_no_calls_writes_nothing(self, tmp_project):
        ScreeningTask(tmp_project, _mock_reviewer()).run()
        assert tmp_project.db.api_call_summary(tmp_project.project_id) == []


class TestClearMockResults:
    def test_clear_removes_only_mock_rows(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        ScreeningTask(tmp_project, _mock_reviewer()).run()  # reviewer_id mock:mock
        db.insert_screening_decision(ScreeningDecision(
            decision="exclude", reasoning="real ai", reviewer_type="ai",
            reviewer_id="anthropic:claude", source_id=sid, stage="abstract",
        ))
        db.insert_screening_decision(ScreeningDecision(
            decision="include", reasoning="human", reviewer_type="human",
            reviewer_id="amber", source_id=sid, stage="abstract",
        ))
        cleared = db.clear_mock_ai_decisions(tmp_project.project_id)
        assert cleared == 1
        left = [(d["reviewer_type"], d["reviewer_id"]) for d in _all_decisions(db, sid)]
        assert ("ai", "mock:mock") not in left
        assert ("ai", "anthropic:claude") in left and ("human", "amber") in left

    def test_cleared_source_is_rescreenable(self, tmp_project):
        """The 0.20 real-run flow: clear mock rows first, then run — the source is picked up
        again instead of being skipped as already-screened."""
        db = tmp_project.db
        _add_source(tmp_project)
        ScreeningTask(tmp_project, _mock_reviewer()).run()
        assert ScreeningTask(tmp_project, _mock_reviewer()).run().total == 0  # blocked by mock rows
        db.clear_mock_ai_decisions(tmp_project.project_id)
        rerun = ScreeningTask(tmp_project, _mock_reviewer()).run()
        assert rerun.total == 1 and rerun.screened == 1

    def test_clear_mock_extractions_removes_derived_ft_decisions(self, tmp_project):
        from ailr.reviewers import ExtractionResult
        db = tmp_project.db
        sid = _add_source(tmp_project)
        db.insert_extraction(ExtractionResult(
            extractor_type="ai", extractor_id="mock:mock", field_name="design", value="x", source_id=sid,
        ))
        db.insert_screening_decision(ScreeningDecision(  # the decision extraction derives
            decision="include", reasoning="(derived from extraction flag_check)",
            reviewer_type="ai", reviewer_id="mock:mock", source_id=sid, stage="full_text",
        ))
        cleared = db.clear_mock_ai_extractions(tmp_project.project_id)
        assert cleared == 1
        assert db.list_extractions(sid, extractor_type="ai") == []
        assert db.get_latest_ai_decision(sid, stage="full_text") is None


def _all_decisions(db, sid):
    return [dict(r) for r in db._conn.execute(
        "SELECT reviewer_type, reviewer_id FROM screening_decisions WHERE source_id = ?", (sid,)
    ).fetchall()]


class TestStaleDetection:
    def test_decision_under_current_prompt_is_not_stale(self, tmp_project):
        db = tmp_project.db
        pid = tmp_project.project_id
        sid = _add_source(tmp_project)
        version = db.save_prompt_version(pid, "screening", "template", composed="PROMPT_A")
        db.insert_screening_decision(ScreeningDecision(
            decision="include", reasoning="t", reviewer_type="ai", reviewer_id="mock:mock",
            source_id=sid, stage="abstract", prompt_version=version,
        ))
        assert db.stale_ai_screening_source_ids(pid, "PROMPT_A") == set()
        assert db.stale_ai_screening_source_ids(pid, "PROMPT_B") == {sid}

    def test_only_latest_ai_decision_is_checked(self, tmp_project):
        db = tmp_project.db
        pid = tmp_project.project_id
        sid = _add_source(tmp_project)
        v1 = db.save_prompt_version(pid, "screening", "template", composed="OLD")
        v2 = db.save_prompt_version(pid, "screening", "template2", composed="NEW")
        for v in (v1, v2):  # old decision under OLD prompt, re-run under NEW
            db.insert_screening_decision(ScreeningDecision(
                decision="include", reasoning="t", reviewer_type="ai", reviewer_id="mock:mock",
                source_id=sid, stage="abstract", prompt_version=v,
            ))
        assert db.stale_ai_screening_source_ids(pid, "NEW") == set()

    def test_decision_without_a_version_is_stale(self, tmp_project):
        db = tmp_project.db
        pid = tmp_project.project_id
        sid = _add_source(tmp_project)
        db.insert_screening_decision(ScreeningDecision(
            decision="include", reasoning="t", reviewer_type="ai", reviewer_id="mock:mock",
            source_id=sid, stage="abstract",
        ))
        assert db.stale_ai_screening_source_ids(pid, "CURRENT") == {sid}
