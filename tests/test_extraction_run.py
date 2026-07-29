"""AI extraction run orchestration with the mock client (no API):
- candidates = abstract-includes with markdown; missing files are skipped, not errors
- flag_check lands as a '_flag_check' row AND derives a full-text AI screening decision
- already-extracted sources are skipped unless force=True
- batch mode lands everything; clearing mock results makes sources re-extractable
"""

from pathlib import Path

from ailr.core.source import Source
from ailr.llm.mock import MockLLMClient, synth_from_tool_schema
from ailr.reviewers import LLMReviewer, ScreeningDecision
from ailr.tasks.extract import ExtractionTask


def _add_source(project, title, include=True, md_file=True, md_path=True):
    sid = project.db.insert_source(Source(
        title=title, abstract="An abstract.", project_id=project.project_id,
    ))
    if include:
        project.db.insert_screening_decision(ScreeningDecision(
            decision="include", reasoning="t", reviewer_type="human",
            reviewer_id="amber", source_id=sid, stage="abstract",
        ))
    if md_path:
        rel = Path("data/markdown") / f"{sid}.md"
        if md_file:
            target = project.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# Paper\n\nFull text about dyadic interaction.", encoding="utf-8")
        project.db.update_markdown_path(sid, rel)
    return sid


def _mock_reviewer():
    # Same shape as the UI's mock path: fabricate a schema-shaped response per call.
    client = MockLLMClient(model="mock-extract", response_fn=lambda _s, _u, ts: synth_from_tool_schema(ts))
    return LLMReviewer(client)


class TestExtractionRun:
    def test_run_extracts_and_derives_the_ft_decision(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, "Candidate")
        summary = ExtractionTask(tmp_project, _mock_reviewer()).run()
        assert summary.total_candidates == 1 and summary.extracted == 1 and summary.failed == 0
        assert db.has_extraction(sid, extractor_type="ai") is True
        assert db.get_flag_check(sid, extractor_type="ai")  # '_flag_check' row landed
        ft = db.get_latest_ai_decision(sid, stage="full_text")
        assert ft is not None and ft["decision"] == "include"  # synth verdicts are all PASS
        assert ft["reviewer_id"] == "mock:mock-extract"

    def test_candidates_are_abstract_includes_with_markdown(self, tmp_project):
        _add_source(tmp_project, "not included", include=False)          # md but no include
        _add_source(tmp_project, "included, no md path", md_path=False)  # include but no markdown
        gone = _add_source(tmp_project, "md file deleted", md_file=False)  # path set, file missing
        summary = ExtractionTask(tmp_project, _mock_reviewer()).run()
        assert summary.total_candidates == 1  # only the md-file-deleted one qualifies as candidate
        assert summary.skipped_no_markdown == 1 and summary.extracted == 0
        assert tmp_project.db.has_extraction(gone, extractor_type="ai") is False

    def test_second_run_skips_done_and_force_redoes(self, tmp_project):
        _add_source(tmp_project, "Candidate")
        ExtractionTask(tmp_project, _mock_reviewer()).run()
        again = ExtractionTask(tmp_project, _mock_reviewer()).run()
        assert again.skipped_already_done == 1 and again.extracted == 0
        forced = ExtractionTask(tmp_project, _mock_reviewer()).run(force=True)
        assert forced.extracted == 1

    def test_only_includes_false_extracts_any_source_with_markdown(self, tmp_project):
        sid = _add_source(tmp_project, "no include vote", include=False)
        assert ExtractionTask(tmp_project, _mock_reviewer()).run().total_candidates == 0
        summary = ExtractionTask(tmp_project, _mock_reviewer()).run(only_includes=False)
        assert summary.extracted == 1
        assert tmp_project.db.has_extraction(sid, extractor_type="ai")

    def test_batch_mode_lands_rows_and_ft_decisions(self, tmp_project):
        db = tmp_project.db
        sids = [_add_source(tmp_project, f"P{i}") for i in range(3)]
        summary = ExtractionTask(tmp_project, _mock_reviewer()).run(batch=True)
        assert summary.extracted == 3
        for sid in sids:
            assert db.has_extraction(sid, extractor_type="ai")
            assert db.get_latest_ai_decision(sid, stage="full_text") is not None

    def test_clear_mock_makes_the_source_extractable_again(self, tmp_project):
        """The 0.20 real-run flow: clear mock rows first, then run — no skipped-as-done."""
        db = tmp_project.db
        sid = _add_source(tmp_project, "Candidate")
        ExtractionTask(tmp_project, _mock_reviewer()).run()
        db.clear_mock_ai_extractions(tmp_project.project_id)
        assert db.has_extraction(sid, extractor_type="ai") is False
        assert db.get_latest_ai_decision(sid, stage="full_text") is None  # derived decision gone too
        rerun = ExtractionTask(tmp_project, _mock_reviewer()).run()
        assert rerun.extracted == 1 and rerun.skipped_already_done == 0

    def test_extraction_rows_carry_values_and_quotes(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, "Candidate")
        ExtractionTask(tmp_project, _mock_reviewer()).run()
        rows = db.list_extractions(sid, extractor_type="ai")
        field_rows = [r for r in rows if r["field_name"] != "_submitted"]
        assert field_rows
        for r in field_rows:
            assert r["value"] is not None  # every schema field got a fabricated value
