"""AI screening run orchestration with the mock client (no API):
- only unscreened sources are processed; a second run is a no-op
- no-abstract sources get a placeholder 'uncertain' (so they aren't retried forever)
- batch mode buffers and lands everything
- 'Clear mock AI results' removes ONLY mock rows; a cleared source is re-screenable
- 'AI outdated' (stale) detection compares the decision's composed prompt vs the current one
"""

import json

from ailr.llm.mock import MockLLMClient, synth_from_tool_schema
from ailr.reviewers import LLMReviewer, ScreeningDecision
from ailr.tasks.screen import ScreeningTask
from tests.helpers import INCLUDE_RESPONSE, add_source, screen_reviewer

_EXCLUDE_RESPONSE = {**INCLUDE_RESPONSE, "decision": "exclude", "reasoning": "mock says no"}


def _add_source(project, title="Paper", abstract="An abstract."):
    return add_source(project, title, abstract=abstract)


class TestScreeningRun:
    def test_a_full_text_ai_verdict_does_not_hide_a_paper_from_abstract_screening(self, tmp_project):
        """Extraction writes the AI's full-text verdict; a paper with one but no abstract verdict
        still needs abstract screening."""
        db = tmp_project.db
        sid = _add_source(tmp_project)
        db.insert_screening_decision(ScreeningDecision(
            decision="include", reasoning="derived from extraction", reviewer_type="ai",
            reviewer_id="gpt", source_id=sid, stage="full_text",
        ))
        summary = ScreeningTask(tmp_project, screen_reviewer()).run()
        assert summary.screened == 1
        assert db.get_latest_ai_decision(sid, "abstract")["decision"] == "include"

    def test_the_models_quotes_and_criteria_are_stored_with_the_decision(self, tmp_project):
        """The canned response elsewhere carries empty lists, so nothing showed these being kept,
        and the screening cross-check has nothing to verify without them."""
        sid = _add_source(tmp_project)
        response = {**INCLUDE_RESPONSE, "evidence_quotes": ["mothers and infants were filmed"],
                    "matched_criteria": ["B1"]}
        ScreeningTask(tmp_project, screen_reviewer(response)).run()
        stored = tmp_project.db.get_latest_ai_decision(sid, "abstract")
        assert stored["evidence_quotes"] == ["mothers and infants were filmed"]
        assert stored["matched_criteria"] == ["B1"]

    def test_per_criterion_verdicts_are_asked_for_unless_switched_off(self, tmp_project):
        db = tmp_project.db
        reviewer = LLMReviewer(MockLLMClient(response_fn=lambda _s, _u, ts: synth_from_tool_schema(ts)))
        asked = _add_source(tmp_project, "default run")
        ScreeningTask(tmp_project, reviewer).run()
        not_asked = _add_source(tmp_project, "run with flag_check off")
        ScreeningTask(tmp_project, reviewer).run(flag_check=False)

        checks = db.get_screening_flag_checks([asked, not_asked], stage="abstract")
        assert checks.get(asked) and not checks.get(not_asked)

    def test_run_screens_all_unscreened(self, tmp_project):
        db = tmp_project.db
        sids = [_add_source(tmp_project, f"P{i}") for i in range(3)]
        summary = ScreeningTask(tmp_project, screen_reviewer()).run()
        assert summary.total == 3 and summary.screened == 3 and summary.include == 3
        assert summary.failed == 0
        for sid in sids:
            latest = db.get_latest_ai_decision(sid, "abstract")
            assert latest["decision"] == "include"
            assert latest["reviewer_id"] == "mock:mock"

    def test_second_run_skips_already_screened(self, tmp_project):
        _add_source(tmp_project)
        ScreeningTask(tmp_project, screen_reviewer()).run()
        again = ScreeningTask(tmp_project, screen_reviewer()).run()
        assert again.total == 0 and again.screened == 0
        assert tmp_project.db.count_screening_decisions(tmp_project.project_id, reviewer_type="ai") == 1

    def test_human_votes_do_not_block_the_ai(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        db.insert_screening_decision(ScreeningDecision(
            decision="exclude", reasoning="t", reviewer_type="human",
            reviewer_id="amber", source_id=sid, stage="abstract",
        ))
        summary = ScreeningTask(tmp_project, screen_reviewer()).run()
        assert summary.screened == 1  # unscreened is per reviewer_type

    def test_no_abstract_gets_a_placeholder_uncertain(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, "no abstract", abstract=None)
        client = MockLLMClient(response=INCLUDE_RESPONSE)
        summary = ScreeningTask(tmp_project, LLMReviewer(client)).run()
        assert summary.skipped_no_abstract == 1
        assert client.call_count == 0  # no LLM call for it
        latest = db.get_latest_ai_decision(sid, "abstract")
        assert latest["decision"] == "uncertain" and latest["confidence"] == 1
        # and it is not re-attempted on the next run
        assert ScreeningTask(tmp_project, screen_reviewer()).run().total == 0

    def test_batch_mode_lands_everything(self, tmp_project):
        [_add_source(tmp_project, f"P{i}") for i in range(4)]
        summary = ScreeningTask(tmp_project, screen_reviewer()).run(batch=True)
        assert summary.screened == 4
        assert tmp_project.db.count_screening_decisions(tmp_project.project_id, reviewer_type="ai") == 4

    def test_limit_caps_the_run(self, tmp_project):
        [_add_source(tmp_project, f"P{i}") for i in range(3)]
        summary = ScreeningTask(tmp_project, screen_reviewer()).run(limit=2)
        assert summary.total == 2 and summary.screened == 2

    def test_raw_output_is_stored_as_json(self, tmp_project):
        """0.24 regression: raw_output must be JSON, not a Python repr."""
        db = tmp_project.db
        sid = _add_source(tmp_project)
        ScreeningTask(tmp_project, screen_reviewer()).run()
        row = db._conn.execute(
            "SELECT raw_output FROM screening_decisions WHERE source_id = ?", (sid,)
        ).fetchone()
        assert json.loads(row["raw_output"])["decision"] == "include"


class TestForcedRescreen:
    """force=True is the only way to re-judge papers under an edited prompt/criteria: the normal
    pass skips anything this reviewer type already decided."""

    def test_force_rejudges_already_screened_sources(self, tmp_project):
        sid = _add_source(tmp_project)
        ScreeningTask(tmp_project, screen_reviewer()).run()
        summary = ScreeningTask(tmp_project, screen_reviewer(_EXCLUDE_RESPONSE)).run(force=True)
        assert summary.total == 1 and summary.screened == 1
        assert tmp_project.db.get_latest_ai_decision(sid, "abstract")["decision"] == "exclude"

    def test_force_appends_and_keeps_the_earlier_decision(self, tmp_project):
        _add_source(tmp_project)
        ScreeningTask(tmp_project, screen_reviewer()).run()
        ScreeningTask(tmp_project, screen_reviewer(_EXCLUDE_RESPONSE)).run(force=True)
        assert tmp_project.db.count_screening_decisions(tmp_project.project_id, reviewer_type="ai") == 2

    def test_without_force_the_second_run_still_skips(self, tmp_project):
        _add_source(tmp_project)
        ScreeningTask(tmp_project, screen_reviewer()).run()
        assert ScreeningTask(tmp_project, screen_reviewer()).run(force=False).total == 0

    def test_force_does_not_stack_placeholders_for_missing_abstracts(self, tmp_project):
        """The placeholder does not depend on the prompt, so re-running must not add a copy."""
        _add_source(tmp_project, "no abstract", abstract=None)
        ScreeningTask(tmp_project, screen_reviewer()).run()
        summary = ScreeningTask(tmp_project, screen_reviewer()).run(force=True)
        assert summary.skipped_no_abstract == 1 and summary.screened == 0
        assert tmp_project.db.count_screening_decisions(tmp_project.project_id, reviewer_type="ai") == 1

    def test_force_reaches_sources_that_were_never_screened(self, tmp_project):
        _add_source(tmp_project, "first")
        ScreeningTask(tmp_project, screen_reviewer()).run()
        _add_source(tmp_project, "added later")
        assert ScreeningTask(tmp_project, screen_reviewer()).run(force=True).screened == 2


class TestApiTelemetry:
    """Token rows are buffered and written once at the end of a run instead of one round trip per
    paper. The rows themselves are still per call, and carry no spend estimate.
    """

    def test_one_row_per_call_written_after_the_run(self, tmp_project):
        [_add_source(tmp_project, f"P{i}") for i in range(3)]
        summary = ScreeningTask(tmp_project, screen_reviewer()).run()

        rows = tmp_project.db.api_call_summary(tmp_project.project_id)
        assert len(rows) == 1                       # one (provider, model) group
        assert rows[0]["calls"] == 3
        assert rows[0]["input_tokens"] == summary.total_input_tokens > 0
        assert rows[0]["output_tokens"] == summary.total_output_tokens > 0

    def test_no_spend_estimate_is_reported(self, tmp_project):
        _add_source(tmp_project)
        ScreeningTask(tmp_project, screen_reviewer()).run()

        assert "cost_estimate" not in tmp_project.db.api_call_summary(tmp_project.project_id)[0]

    def test_a_run_that_made_no_calls_writes_nothing(self, tmp_project):
        ScreeningTask(tmp_project, screen_reviewer()).run()
        assert tmp_project.db.api_call_summary(tmp_project.project_id) == []


class TestClearMockResults:
    def test_clear_removes_only_mock_rows(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        ScreeningTask(tmp_project, screen_reviewer()).run()  # reviewer_id mock:mock
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
        ScreeningTask(tmp_project, screen_reviewer()).run()
        assert ScreeningTask(tmp_project, screen_reviewer()).run().total == 0  # blocked by mock rows
        db.clear_mock_ai_decisions(tmp_project.project_id)
        rerun = ScreeningTask(tmp_project, screen_reviewer()).run()
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

    def test_decision_without_a_version_is_unknown_not_stale(self, tmp_project):
        """Early runs and imported results carry no prompt version. Calling those outdated badged
        every such paper forever, which is noise that teaches you to ignore the badge."""
        db = tmp_project.db
        pid = tmp_project.project_id
        sid = _add_source(tmp_project)
        db.insert_screening_decision(ScreeningDecision(
            decision="include", reasoning="t", reviewer_type="ai", reviewer_id="mock:mock",
            source_id=sid, stage="abstract",
        ))
        assert db.stale_ai_screening_source_ids(pid, "CURRENT") == set()

    def test_a_version_that_stored_no_composed_prompt_is_unknown_too(self, tmp_project):
        db = tmp_project.db
        pid = tmp_project.project_id
        sid = _add_source(tmp_project)
        version = db.save_prompt_version(pid, "screening", "template", composed="")
        db.insert_screening_decision(ScreeningDecision(
            decision="include", reasoning="t", reviewer_type="ai", reviewer_id="mock:mock",
            source_id=sid, stage="abstract", prompt_version=version,
        ))
        assert db.stale_ai_screening_source_ids(pid, "CURRENT") == set()

    def test_source_ids_narrows_the_scan(self, tmp_project):
        db = tmp_project.db
        pid = tmp_project.project_id
        version = db.save_prompt_version(pid, "screening", "template", composed="OLD")
        a, b = _add_source(tmp_project), _add_source(tmp_project)
        for sid in (a, b):
            db.insert_screening_decision(ScreeningDecision(
                decision="include", reasoning="t", reviewer_type="ai", reviewer_id="mock:mock",
                source_id=sid, stage="abstract", prompt_version=version,
            ))
        assert db.stale_ai_screening_source_ids(pid, "NEW") == {a, b}
        assert db.stale_ai_screening_source_ids(pid, "NEW", source_ids=[a]) == {a}
        assert db.stale_ai_screening_source_ids(pid, "NEW", source_ids=[]) == set()
