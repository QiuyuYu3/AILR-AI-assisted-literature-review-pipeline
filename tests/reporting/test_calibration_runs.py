"""The quick-test and calibration runs: which papers they take, what a failed call leaves, what they write."""

import pytest

from ailr.exceptions import AILRError
from ailr.reviewers import ScreeningDecision
from ailr.tasks.calibrate import (
    CalibrationTask,
    ExtractionQuickTestTask,
    QuickTestTask,
    sample_agreement,
)
from tests.helpers import (
    add_source,
    count_decisions,
    extract_reviewer,
    screen_reviewer,
    set_config,
    settle,
    vote,
)


class _Screener:
    """Includes everything except the titles it is told to fail on; records what it was asked."""

    reviewer_type = "ai"
    reviewer_id = "fake"

    def __init__(self, fail_on=()):
        self.fail_on, self.seen = set(fail_on), []

    def screen(self, source, *_args, **_kwargs):
        self.seen.append(source.id)
        if source.title in self.fail_on:
            raise RuntimeError(f"provider refused {source.title}")
        return ScreeningDecision(decision="include", reasoning="fits", reviewer_type="ai", reviewer_id="fake", confidence=8)


class _Extractor:
    """The schema-shaped mock extractor, failing on the titles it is told to."""

    def __init__(self, fail_on=()):
        self.fail_on, self._inner = set(fail_on), extract_reviewer()
        self.reviewer_type, self.reviewer_id = self._inner.reviewer_type, self._inner.reviewer_id

    def extract(self, *, source, **kwargs):
        if source.title in self.fail_on:
            raise RuntimeError(f"provider refused {source.title}")
        return self._inner.extract(source=source, **kwargs)


def _with_abstracts(project, *titles):
    return [add_source(project, t, abstract=f"About {t}.") for t in titles]


def _test_decision_ids(project, run_id):
    return sorted(r["source_id"] for r in project.db.list_test_decisions(run_id))


def _progress():
    calls = []
    return calls, lambda idx, total, decision, exc: calls.append((idx, total, decision is not None, exc is not None))


# ----- Abstract quick test -----

class TestQuickTest:
    def test_picked_papers_are_tested_whatever_n_says(self, tmp_project):
        a, _, c = _with_abstracts(tmp_project, "A", "B", "C")
        screener = _Screener()

        summary = QuickTestTask(tmp_project, screener).run(n=1, source_ids=[a, c])

        assert (summary.sample_size, summary.candidates_available) == (2, 2)
        assert screener.seen == [a, c]
        assert _test_decision_ids(tmp_project, summary.run_id) == [a, c]

    def test_nothing_to_test_calls_nothing(self, tmp_project):
        add_source(tmp_project, "No abstract")
        screener = _Screener()

        summary = QuickTestTask(tmp_project, screener).run(n=5)

        assert (summary.sample_size, summary.candidates_available) == (0, 0)
        assert screener.seen == []
        assert tmp_project.db.list_test_decisions(summary.run_id) == []

    def test_a_failed_call_is_listed_and_the_rest_still_run(self, tmp_project):
        a, b, c = _with_abstracts(tmp_project, "A", "B", "C")
        calls, on_progress = _progress()

        summary = QuickTestTask(tmp_project, _Screener(fail_on={"B"})).run(source_ids=[a, b, c], on_progress=on_progress)

        assert summary.failed == 1
        assert summary.failures == [{"source_id": b, "title": "B", "error": "provider refused B"}]
        assert summary.ai_counts == {"include": 2, "exclude": 0, "uncertain": 0}
        assert _test_decision_ids(tmp_project, summary.run_id) == [a, c]
        assert calls == [(1, 3, True, False), (2, 3, False, True), (3, 3, True, False)]
        assert count_decisions(tmp_project.db, tmp_project.project_id) == 0     # trial runs stay out of the record


# ----- Extraction quick test -----

class TestExtractionQuickTest:
    def test_picked_papers_are_tested_whatever_n_says(self, tmp_project):
        a, _, c = (add_source(tmp_project, t, md_on_disk=True) for t in ("A", "B", "C"))

        summary = ExtractionQuickTestTask(tmp_project, _Extractor()).run(n=1, source_ids=[a, c])

        assert summary.sample_size == 2
        assert sorted(r["source_id"] for r in tmp_project.db.list_test_extractions(summary.run_id)) == [a, c]

    def test_nothing_with_markdown_calls_nothing(self, tmp_project):
        add_source(tmp_project, "No markdown")

        summary = ExtractionQuickTestTask(tmp_project, _Extractor(fail_on={"No markdown"})).run(n=3)

        assert (summary.sample_size, summary.failed) == (0, 0)

    def test_a_markdown_file_gone_from_disk_is_a_listed_failure(self, tmp_project):
        sid = add_source(tmp_project, "Gone", with_md=True)
        calls, on_progress = _progress()

        summary = ExtractionQuickTestTask(tmp_project, _Extractor()).run(source_ids=[sid], on_progress=on_progress)

        assert summary.failures == [{"source_id": sid, "title": "Gone", "error": "markdown file missing"}]
        assert calls == [(1, 1, False, False)]
        assert tmp_project.db.list_test_extractions(summary.run_id) == []

    def test_a_failed_call_is_listed_and_the_rest_still_run(self, tmp_project):
        a, b = (add_source(tmp_project, t, md_on_disk=True) for t in ("A", "B"))
        calls, on_progress = _progress()

        summary = ExtractionQuickTestTask(tmp_project, _Extractor(fail_on={"A"})).run(source_ids=[a, b], on_progress=on_progress)

        assert summary.failures == [{"source_id": a, "title": "A", "error": "provider refused A"}]
        assert [r["source_id"] for r in tmp_project.db.list_test_extractions(summary.run_id)] == [b]
        assert calls == [(1, 2, False, True), (2, 2, False, False)]
        assert sum(summary.decision_counts.values()) == 1         # B's verdict, from its flag_check


# ----- Calibration round -----

class TestCalibrationSetup:
    def test_an_unknown_stage_is_refused(self, tmp_project):
        with pytest.raises(ValueError, match="Unknown stage"):
            CalibrationTask(tmp_project, _Screener(), stage="abstract")

    def test_independent_workflow_is_refused(self, tmp_project):
        project = set_config(tmp_project, "screening", workflow="independent")
        with pytest.raises(AILRError, match="independent"):
            CalibrationTask(project, _Screener(), stage="screening")

    @pytest.mark.parametrize("calibration,n_arg,available,expected", [
        ({}, 7, 5, 5),                                      # an explicit n, capped at what is there
        ({"n": 12}, None, 40, 12),
        ({"n": 12}, None, 8, 8),
        ({"fraction": 0.5, "min": 3}, None, 40, 20),
        ({"fraction": 0.05, "min": 3}, None, 40, 3),        # the floor
        ({"fraction": 0.05, "min": 30}, None, 10, 10),      # the floor, capped at what is there
    ])
    @pytest.mark.parametrize("stage,other", [("screening", "extraction"), ("extraction", "screening")])
    def test_sample_size(self, tmp_project, calibration, n_arg, available, expected, stage, other):
        set_config(tmp_project, other, calibration={"n": 1})      # each stage reads its own settings
        project = set_config(tmp_project, stage, calibration=calibration)
        assert CalibrationTask(project, _Screener(), stage=stage).determine_sample_size(n_arg, available) == expected


class TestScreeningCalibration:
    def test_nothing_left_to_draw_calls_nothing(self, tmp_project):
        screener = _Screener()
        summary = CalibrationTask(tmp_project, screener).run(n=5)
        assert (summary.sample_size, summary.sample_round) == (0, 1)
        assert screener.seen == []

    def test_an_existing_ai_decision_is_reused_not_bought_again(self, tmp_project):
        done, fresh = _with_abstracts(tmp_project, "Done", "Fresh")
        vote(tmp_project.db, done, "exclude", "earlier-model", stage="abstract", reviewer_type="ai")
        screener = _Screener()
        calls, on_progress = _progress()

        summary = CalibrationTask(tmp_project, screener).run(n=2, on_progress=on_progress)

        assert screener.seen == [fresh]
        assert summary.ai_counts == {"include": 1, "exclude": 1, "uncertain": 0}
        assert sorted(has_decision for _, _, has_decision, _ in calls) == [False, True]   # the reused one carries none

    def test_a_failed_call_is_listed_and_leaves_no_decision(self, tmp_project):
        good, bad = _with_abstracts(tmp_project, "Good", "Bad")

        summary = CalibrationTask(tmp_project, _Screener(fail_on={"Bad"})).run(n=2)

        assert summary.failures == [{"source_id": bad, "title": "Bad", "error": "provider refused Bad"}]
        assert tmp_project.db.get_latest_ai_decision(bad) is None
        assert tmp_project.db.get_latest_ai_decision(good)["decision"] == "include"

    def test_llm_calls_are_logged_for_the_token_report(self, tmp_project):
        _with_abstracts(tmp_project, "A", "B")
        CalibrationTask(tmp_project, screen_reviewer()).run(n=2)
        assert [r["calls"] for r in tmp_project.db.api_call_summary(tmp_project.project_id)] == [2]

    def test_a_second_round_draws_new_papers(self, tmp_project):
        _with_abstracts(tmp_project, "A", "B", "C")
        first = CalibrationTask(tmp_project, _Screener()).run(n=2)
        screener = _Screener()

        second = CalibrationTask(tmp_project, screener).run(n=2)

        assert (first.sample_round, second.sample_round) == (1, 2)
        assert (second.candidates_available, second.sample_size) == (1, 1)
        drawn = {s.id for s in tmp_project.db.list_calibration_sample(tmp_project.project_id, "screening", sample_round=1)}
        assert screener.seen and set(screener.seen).isdisjoint(drawn)

    def test_agreement_is_reported_once_humans_have_decided(self, tmp_project):
        a, b = _with_abstracts(tmp_project, "A", "B")
        vote(tmp_project.db, a, "include", "amber", stage="abstract")
        vote(tmp_project.db, b, "exclude", "amber", stage="abstract")

        summary = CalibrationTask(tmp_project, _Screener()).run(n=2)

        assert (summary.paired_count, summary.agreement) == (2, 0.5)
        assert summary.human_counts == {"include": 1, "exclude": 1, "uncertain": 0}


class TestExtractionCalibration:
    def _candidate(self, project, title, **kwargs):
        sid = add_source(project, title, **kwargs)
        settle(project.db, sid, "include", stage="abstract")
        return sid

    def test_flag_check_off_is_refused(self, tmp_project):
        self._candidate(tmp_project, "A", md_on_disk=True)
        project = set_config(tmp_project, "extraction", flag_check=False)
        with pytest.raises(AILRError, match="flag_check"):
            CalibrationTask(project, _Extractor(), stage="extraction").run(n=1)

    def test_the_verdicts_come_from_extracting_the_sample(self, tmp_project):
        sid = self._candidate(tmp_project, "A", md_on_disk=True)

        summary = CalibrationTask(tmp_project, extract_reviewer(), stage="extraction").run(n=1)

        assert summary.failed == 0
        assert sum(summary.ai_counts.values()) == 1
        assert tmp_project.db.get_latest_ai_decision(sid, stage="full_text") is not None

    def test_a_markdown_file_gone_from_disk_is_reported(self, tmp_project):
        self._candidate(tmp_project, "Gone", with_md=True)

        summary = CalibrationTask(tmp_project, extract_reviewer(), stage="extraction").run(n=1)

        assert summary.failed == 1
        assert summary.failures == [{"error": "1 paper(s) in the sample have no markdown file on disk"}]


def test_sample_agreement_reads_one_stage(tmp_project):
    a, b = _with_abstracts(tmp_project, "A", "B")
    for sid, ai, human in ((a, "include", "include"), (b, "include", "exclude")):
        vote(tmp_project.db, sid, ai, "gpt", stage="abstract", reviewer_type="ai")
        vote(tmp_project.db, sid, human, "amber", stage="abstract")
    vote(tmp_project.db, b, "include", "amber", stage="full_text")

    assert sample_agreement(tmp_project, [a, b])["agreement"] == 0.5
    assert sample_agreement(tmp_project, [a, b], stage="full_text")["paired_count"] == 0
