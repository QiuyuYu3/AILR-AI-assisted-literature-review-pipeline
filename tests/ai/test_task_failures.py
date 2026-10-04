"""What the run tasks do when a call fails.

Every other task test asserts summary.failed == 0, so the except branch in ScreeningTask,
ExtractionTask and PreprocessTask had never run. The invariant that matters most is the one
extract.py documents but nothing checked: a forced re-extract that dies must leave the previous
extraction standing rather than half-replacing it.
"""

from pathlib import Path

from ailr.core.source import Source
from ailr.exceptions import LLMError
from ailr.llm.mock import MockLLMClient, synth_from_tool_schema
from ailr.preprocess import PDFConverter
from ailr.reviewers import LLMReviewer, Reviewer
from ailr.tasks.extract import ExtractionTask
from ailr.tasks.preprocess import PreprocessTask
from ailr.tasks.screen import ScreeningTask
from tests.helpers import add_source, extract_reviewer, screen_reviewer, vote


class _BoomReviewer(Reviewer):
    """Wraps a working reviewer and raises for the titles it is told to (all of them by default)."""

    def __init__(self, inner, boom_titles=None, exc=None):
        self._inner = inner
        self._boom_titles = boom_titles
        self._exc = exc or RuntimeError("provider said no")

    @property
    def reviewer_type(self) -> str:
        return self._inner.reviewer_type

    @property
    def reviewer_id(self) -> str:
        return self._inner.reviewer_id

    def _fails(self, source) -> bool:
        return self._boom_titles is None or source.title in self._boom_titles

    def screen(self, source, *args, **kwargs):
        if self._fails(source):
            raise self._exc
        return self._inner.screen(source, *args, **kwargs)

    def extract(self, source, *args, **kwargs):
        if self._fails(source):
            raise self._exc
        return self._inner.extract(source, *args, **kwargs)


def _abstract_source(project, title="Paper"):
    return add_source(project, title, abstract="An abstract.")


def _extractable_source(project, title="Paper"):
    """An abstract-include with markdown on disk: an extraction candidate."""
    sid = add_source(project, title, abstract="An abstract.", md_on_disk=True)
    vote(project.db, sid, "include", "amber", stage="abstract")
    return sid


class TestScreeningFailures:
    def test_a_failing_call_is_counted_not_raised(self, tmp_project):
        sids = [_abstract_source(tmp_project, f"P{i}") for i in range(3)]
        summary = ScreeningTask(tmp_project, _BoomReviewer(screen_reviewer())).run()

        assert summary.total == 3 and summary.screened == 0 and summary.failed == 3
        assert {f["source_id"] for f in summary.failures} == set(sids)
        assert all("provider said no" in f["error"] for f in summary.failures)
        assert tmp_project.db.count_screening_decisions(tmp_project.project_id, reviewer_type="ai") == 0

    def test_one_bad_paper_does_not_take_the_run_down(self, tmp_project):
        db = tmp_project.db
        ok = _abstract_source(tmp_project, "fine")
        bad = _abstract_source(tmp_project, "boom")

        summary = ScreeningTask(tmp_project, _BoomReviewer(screen_reviewer(), {"boom"})).run()

        assert summary.screened == 1 and summary.failed == 1
        assert db.get_latest_ai_decision(ok, "abstract")["decision"] == "include"
        assert db.get_latest_ai_decision(bad, "abstract") is None
        assert [f["title"] for f in summary.failures] == ["boom"]

    def test_on_progress_is_handed_the_exception(self, tmp_project):
        _abstract_source(tmp_project, "fine")
        _abstract_source(tmp_project, "boom")
        seen: list = []

        ScreeningTask(tmp_project, _BoomReviewer(screen_reviewer(), {"boom"})).run(
            on_progress=lambda done, total, decision, err: seen.append((decision, err))
        )

        assert len(seen) == 2
        errors = [e for _d, e in seen if e is not None]
        assert len(errors) == 1 and isinstance(errors[0], RuntimeError)
        # the failed call reports no decision alongside its error
        assert [d for d, e in seen if e is not None] == [None]

    def test_a_failed_paper_is_screened_again_on_the_next_run(self, tmp_project):
        """Nothing was written for it, so it is still unscreened rather than quietly skipped."""
        _abstract_source(tmp_project, "boom")
        ScreeningTask(tmp_project, _BoomReviewer(screen_reviewer())).run()

        again = ScreeningTask(tmp_project, screen_reviewer()).run()
        assert again.total == 1 and again.screened == 1

    def test_batch_mode_still_lands_the_papers_that_worked(self, tmp_project):
        """The buffered write happens after the loop, so a failure inside it must not cost the
        successful decisions their INSERT."""
        db = tmp_project.db
        ok = _abstract_source(tmp_project, "fine")
        _abstract_source(tmp_project, "boom")

        summary = ScreeningTask(tmp_project, _BoomReviewer(screen_reviewer(), {"boom"})).run(batch=True)

        assert summary.screened == 1 and summary.failed == 1
        assert db.count_screening_decisions(tmp_project.project_id, reviewer_type="ai") == 1
        assert db.get_latest_ai_decision(ok, "abstract")["decision"] == "include"


class TestExtractionFailures:
    def test_failure_is_counted_and_names_the_exception_type(self, tmp_project):
        """Several DB/IO errors stringify to an empty message, so the type is part of the report."""
        sid = _extractable_source(tmp_project, "boom")

        summary = ExtractionTask(tmp_project, _BoomReviewer(extract_reviewer())).run()

        assert summary.total_candidates == 1 and summary.extracted == 0 and summary.failed == 1
        [failure] = summary.failures
        assert failure["source_id"] == sid and failure["title"] == "boom"
        assert failure["error"].startswith("RuntimeError: ")
        assert tmp_project.db.has_extraction(sid, extractor_type="ai") is False
        assert tmp_project.db.get_latest_ai_decision(sid, stage="full_text") is None

    def test_a_parse_failure_is_reported_as_an_llm_error(self, tmp_project):
        """_unwrap_value_quote raises LLMError on a payload it cannot parse (parsing itself is
        covered in test_ai_results_parsing); through the task it must fail exactly that paper."""
        _extractable_source(tmp_project, "ok")
        _extractable_source(tmp_project, "boom")
        reviewer = _BoomReviewer(
            extract_reviewer(), {"boom"},
            exc=LLMError("study_design: could not parse. Re-run this paper."),
        )

        summary = ExtractionTask(tmp_project, reviewer).run()

        assert summary.extracted == 1 and summary.failed == 1
        assert summary.failures[0]["error"].startswith("LLMError: ")
        assert "Re-run this paper" in summary.failures[0]["error"]

    def test_a_structured_field_returned_as_broken_json_fails_the_paper(self, tmp_project):
        """The test above stands in a reviewer that raises before any parsing. This one goes
        through the real reviewer: a list-of-objects field that comes back as a JSON string which
        does not parse must fail that paper and store none of it."""
        sid = _extractable_source(tmp_project, "broken")

        def broken(_system, _user, tool_schema):
            out = synth_from_tool_schema(tool_schema)
            out["example_list_of_objects"] = "[not json"
            return out

        reviewer = LLMReviewer(MockLLMClient(model="mock-extract", response_fn=broken))
        summary = ExtractionTask(tmp_project, reviewer).run()

        assert (summary.extracted, summary.failed) == (0, 1)
        assert summary.failures[0]["error"].startswith("LLMError: ")
        assert "example_list_of_objects" in summary.failures[0]["error"]
        assert tmp_project.db.has_extraction(sid, extractor_type="ai") is False

    def test_a_forced_re_extract_that_fails_leaves_the_previous_run_alone(self, tmp_project):
        """extract.py notes where the previous AI run ends BEFORE writing and retires it only once
        the new rows have landed. A failed call must therefore leave the live extraction whole."""
        db = tmp_project.db
        sid = _extractable_source(tmp_project, "Candidate")
        ExtractionTask(tmp_project, extract_reviewer()).run()
        before = {r["field_name"]: r["value"] for r in db.list_extractions(sid, extractor_type="ai")}
        ft_before = db.get_latest_ai_decision(sid, stage="full_text")
        assert before and ft_before is not None

        summary = ExtractionTask(tmp_project, _BoomReviewer(extract_reviewer())).run(force=True)

        assert summary.failed == 1 and summary.extracted == 0
        assert summary.archived == 0
        after = {r["field_name"]: r["value"] for r in db.list_extractions(sid, extractor_type="ai")}
        assert after == before                                   # live rows untouched
        assert db.list_superseded_ai_runs(sid) == []              # and none were retired behind it
        assert db.get_latest_ai_decision(sid, stage="full_text")["decision"] == ft_before["decision"]

    def test_a_failed_paper_is_not_marked_done(self, tmp_project):
        _extractable_source(tmp_project, "boom")
        ExtractionTask(tmp_project, _BoomReviewer(extract_reviewer())).run()

        rerun = ExtractionTask(tmp_project, extract_reviewer()).run()
        assert rerun.skipped_already_done == 0 and rerun.extracted == 1

    def test_batch_mode_lands_the_papers_that_worked(self, tmp_project):
        db = tmp_project.db
        ok = _extractable_source(tmp_project, "fine")
        bad = _extractable_source(tmp_project, "boom")

        summary = ExtractionTask(tmp_project, _BoomReviewer(extract_reviewer(), {"boom"})).run(batch=True)

        assert summary.extracted == 1 and summary.failed == 1
        assert db.has_extraction(ok, extractor_type="ai") is True
        assert db.has_extraction(bad, extractor_type="ai") is False
        assert db.get_latest_ai_decision(ok, stage="full_text") is not None
        assert db.get_latest_ai_decision(bad, stage="full_text") is None


class _StubConverter(PDFConverter):
    """Returns fixed markdown, or raises for the pdf file stems it is told to fail on."""

    def __init__(self, boom_stems=()):
        self._boom = set(boom_stems)

    @property
    def backend_name(self) -> str:
        return "stub"

    def convert(self, pdf_path: Path) -> str:
        if pdf_path.stem in self._boom:
            raise RuntimeError("marker crashed")
        return "# Paper\n\n" + ("Body text about dyadic interaction. " * 100)


def _with_pdf(project, title="Paper"):
    sid = project.db.insert_source(Source(title=title, project_id=project.project_id))
    pdf_dir = project.root / "data" / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    (pdf_dir / f"{sid}.pdf").write_bytes(b"%PDF-1.4 not really a pdf")
    return sid


class TestPreprocessFailures:
    def test_a_conversion_that_crashes_is_reported_not_raised(self, tmp_project):
        sid = _with_pdf(tmp_project, "P")

        summary = PreprocessTask(tmp_project, converter=_StubConverter({str(sid)})).run()

        assert summary.total_pdfs == 1 and summary.converted == 0 and summary.failed == 1
        [failure] = summary.failures
        assert failure["source_id"] == sid and failure["pdf"] == f"{sid}.pdf"
        assert "marker crashed" in failure["error"]

    def test_a_failed_conversion_writes_no_markdown(self, tmp_project):
        """A half-written .md would count as done forever, and the paper would enter the extraction
        queue with an empty full text."""
        sid = _with_pdf(tmp_project, "P")
        PreprocessTask(tmp_project, converter=_StubConverter({str(sid)})).run()

        assert not (tmp_project.root / "data" / "markdown" / f"{sid}.md").exists()
        assert tmp_project.db.get_source(sid).markdown_path is None

    def test_one_bad_pdf_does_not_stop_the_others(self, tmp_project):
        good = _with_pdf(tmp_project, "readable")
        bad = _with_pdf(tmp_project, "corrupt")

        summary = PreprocessTask(tmp_project, converter=_StubConverter({str(bad)})).run()

        assert summary.converted == 1 and summary.failed == 1
        assert (tmp_project.root / "data" / "markdown" / f"{good}.md").exists()
        assert tmp_project.db.get_source(good).markdown_path is not None

    def test_a_failed_pdf_is_converted_again_on_the_next_run(self, tmp_project):
        sid = _with_pdf(tmp_project, "P")
        PreprocessTask(tmp_project, converter=_StubConverter({str(sid)})).run()

        rerun = PreprocessTask(tmp_project, converter=_StubConverter()).run()
        assert rerun.skipped_already_done == 0 and rerun.converted == 1
