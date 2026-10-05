"""The quick-test runs: which papers they take, what a failed call leaves, what they write."""

from ailr.reviewers import ScreeningDecision
from ailr.tasks.calibrate import ExtractionQuickTestTask, QuickTestTask
from tests.helpers import add_source, count_decisions, extract_reviewer, screen_reviewer


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

    def test_llm_calls_are_logged_for_the_token_report(self, tmp_project):
        """Trial verdicts stay out of the record, but the tokens they cost are real."""
        _with_abstracts(tmp_project, "A", "B")
        QuickTestTask(tmp_project, screen_reviewer()).run(n=2)
        assert [r["calls"] for r in tmp_project.db.api_call_summary(tmp_project.project_id)] == [2]


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

    def test_llm_calls_are_logged_for_the_token_report(self, tmp_project):
        add_source(tmp_project, "A", md_on_disk=True)
        ExtractionQuickTestTask(tmp_project, extract_reviewer()).run(n=1)
        assert [r["calls"] for r in tmp_project.db.api_call_summary(tmp_project.project_id)] == [1]
