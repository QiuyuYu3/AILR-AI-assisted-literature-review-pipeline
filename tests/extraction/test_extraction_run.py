"""AI extraction run orchestration with the mock client (no API):
- candidates = abstract-includes with markdown; missing files are skipped, not errors
- flag_check lands as a '_flag_check' row AND derives a full-text AI screening decision
- already-extracted sources are skipped unless force=True
- batch mode lands everything; clearing mock results makes sources re-extractable
"""

from ailr.exceptions import DatabaseError
from ailr.reviewers import ExtractionResult, ScreeningDecision
from ailr.tasks.extract import ExtractionTask
from tests.helpers import add_source, extract_reviewer, vote


def _add_source(project, title, include=True, md_file=True, md_path=True):
    sid = add_source(project, title, abstract="An abstract.", with_md=md_path, md_on_disk=md_path and md_file)
    if include:
        vote(project.db, sid, "include", "amber", stage="abstract")
    return sid


def _count(db, sql, *params) -> int:
    return db._conn.execute(sql, params).fetchone()["n"]


class TestForcedReExtraction:
    """A forced re-extract replaces the AI's own earlier run, and nothing else."""

    def _seed_neighbours(self, db, sid):
        """Work a re-run must leave alone: a human extraction, a human full-text vote, and the
        AI's abstract verdict."""
        db.insert_extraction(ExtractionResult(
            extractor_type="human", extractor_id="amber", field_name="design", value="obs", source_id=sid,
        ))
        for rtype, rid, stage in (("human", "amber", "full_text"), ("ai", "gpt", "abstract")):
            db.insert_screening_decision(ScreeningDecision(
                decision="include", reasoning="t", reviewer_type=rtype, reviewer_id=rid,
                source_id=sid, stage=stage,
            ))

    def test_it_retires_the_earlier_ai_run_and_its_full_text_verdict(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, "Candidate")
        ExtractionTask(tmp_project, extract_reviewer()).run()
        first = [r["id"] for r in db.list_extractions(sid, extractor_type="ai")]
        self._seed_neighbours(db, sid)

        assert ExtractionTask(tmp_project, extract_reviewer()).run(force=True).extracted == 1
        live = [r["id"] for r in db.list_extractions(sid, extractor_type="ai")]
        assert len(live) == len(first) and min(live) > max(first)
        assert _count(db, "SELECT COUNT(*) AS n FROM extractions WHERE source_id = ? "
                          "AND extractor_type = 'ai' AND field_name = '_flag_check'", sid) == 1
        assert _count(db, "SELECT COUNT(*) AS n FROM screening_decisions WHERE source_id = ? "
                          "AND reviewer_type = 'ai' AND stage = 'full_text'", sid) == 1

    def test_it_leaves_human_work_and_the_abstract_verdict_alone(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, "Candidate")
        ExtractionTask(tmp_project, extract_reviewer()).run()
        self._seed_neighbours(db, sid)

        ExtractionTask(tmp_project, extract_reviewer()).run(force=True)
        human = [(r["extractor_id"], r["field_name"]) for r in db.list_extractions(sid, extractor_type="human")]
        assert human == [("amber", "design")]
        assert [d["reviewer_id"] for d in db.get_human_decisions(sid, stage="full_text")] == ["amber"]
        assert db.get_latest_ai_decision(sid, stage="abstract")["reviewer_id"] == "gpt"

    def test_a_failure_part_way_through_leaves_the_previous_run_whole(self, tmp_project, monkeypatch):
        db = tmp_project.db
        sid = _add_source(tmp_project, "Candidate")
        ExtractionTask(tmp_project, extract_reviewer()).run()
        before = [r["id"] for r in db.list_extractions(sid, extractor_type="ai")]
        real_insert, calls = db.insert_extraction, []

        def insert_then_fail(result):
            calls.append(result.field_name)
            if len(calls) == 2:
                raise DatabaseError("disk full")
            return real_insert(result)

        monkeypatch.setattr(db, "insert_extraction", insert_then_fail)
        assert ExtractionTask(tmp_project, extract_reviewer()).run(force=True).failed == 1
        assert [r["id"] for r in db.list_extractions(sid, extractor_type="ai")] == before


class TestExtractionRun:
    def test_run_extracts_and_derives_the_ft_decision(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, "Candidate")
        summary = ExtractionTask(tmp_project, extract_reviewer()).run()
        assert summary.total_candidates == 1 and summary.extracted == 1 and summary.failed == 0
        assert db.has_extraction(sid, extractor_type="ai") is True
        assert db.get_flag_check(sid, extractor_type="ai")  # '_flag_check' row landed
        ft = db.get_latest_ai_decision(sid, stage="full_text")
        assert ft is not None and ft["decision"] == "include"  # synth verdicts are all PASS
        assert ft["reviewer_id"] == "mock:mock-extract"

    def test_clearing_mock_results_keeps_real_ai_and_human_work(self, tmp_project):
        """Every real run starts by clearing mock rows, so the filter is all that stands between
        it and the paid extractions."""
        db = tmp_project.db
        sid = _add_source(tmp_project, "Candidate")
        ExtractionTask(tmp_project, extract_reviewer()).run()
        for rtype, rid in (("ai", "anthropic:claude"), ("human", "amber")):
            db.insert_extraction(ExtractionResult(
                extractor_type=rtype, extractor_id=rid, field_name="design", value="obs", source_id=sid,
            ))
        db.insert_screening_decision(ScreeningDecision(
            decision="include", reasoning="t", reviewer_type="ai", reviewer_id="anthropic:claude",
            source_id=sid, stage="full_text",
        ))

        db.clear_mock_ai_extractions(tmp_project.project_id)
        assert [r["extractor_id"] for r in db.list_extractions(sid, extractor_type="ai")] == ["anthropic:claude"]
        assert [r["extractor_id"] for r in db.list_extractions(sid, extractor_type="human")] == ["amber"]
        assert db.get_latest_ai_decision(sid, stage="full_text")["reviewer_id"] == "anthropic:claude"

    def test_re_extracting_one_paper_leaves_the_others_alone(self, tmp_project):
        db = tmp_project.db
        one, other = _add_source(tmp_project, "One"), _add_source(tmp_project, "Other")
        ExtractionTask(tmp_project, extract_reviewer()).run()
        other_rows = [r["id"] for r in db.list_extractions(other, extractor_type="ai")]

        summary = ExtractionTask(tmp_project, extract_reviewer()).run(force=True, source_ids=[one])
        assert (summary.total_candidates, summary.extracted) == (1, 1)
        assert [r["id"] for r in db.list_extractions(other, extractor_type="ai")] == other_rows

    def test_candidates_are_papers_whose_abstract_screening_settled_on_include(self, tmp_project):
        """The full-text queue's rule: a vote changed to exclude, an adjudicated exclusion, an
        AI-only include and a flagged duplicate are not extracted."""
        db = tmp_project.db

        settled = _add_source(tmp_project, "settled include")
        changed = _add_source(tmp_project, "include changed to exclude")
        vote(db, changed, "exclude", "amber", stage="abstract")
        adjudicated = _add_source(tmp_project, "adjudicated out")
        vote(db, adjudicated, "exclude", "gpt", stage="abstract", reviewer_type="ai")
        db.insert_screening_reconciliation(adjudicated, "exclude", "pi", "", stage="abstract")
        ai_only = _add_source(tmp_project, "AI include only", include=False)
        vote(db, ai_only, "include", "gpt", stage="abstract", reviewer_type="ai")
        db.mark_source_duplicate(_add_source(tmp_project, "flagged duplicate"), True)

        summary = ExtractionTask(tmp_project, extract_reviewer()).run()
        assert summary.total_candidates == 1
        assert db.has_extraction(settled, extractor_type="ai")

    def test_candidates_are_abstract_includes_with_markdown(self, tmp_project):
        _add_source(tmp_project, "not included", include=False)          # md but no include
        _add_source(tmp_project, "included, no md path", md_path=False)  # include but no markdown
        gone = _add_source(tmp_project, "md file deleted", md_file=False)  # path set, file missing
        summary = ExtractionTask(tmp_project, extract_reviewer()).run()
        assert summary.total_candidates == 1  # only the md-file-deleted one qualifies as candidate
        assert summary.skipped_no_markdown == 1 and summary.extracted == 0
        assert tmp_project.db.has_extraction(gone, extractor_type="ai") is False

    def test_second_run_skips_done_and_force_redoes(self, tmp_project):
        _add_source(tmp_project, "Candidate")
        ExtractionTask(tmp_project, extract_reviewer()).run()
        again = ExtractionTask(tmp_project, extract_reviewer()).run()
        assert again.skipped_already_done == 1 and again.extracted == 0
        forced = ExtractionTask(tmp_project, extract_reviewer()).run(force=True)
        assert forced.extracted == 1

    def test_only_includes_false_extracts_any_source_with_markdown(self, tmp_project):
        sid = _add_source(tmp_project, "no include vote", include=False)
        assert ExtractionTask(tmp_project, extract_reviewer()).run().total_candidates == 0
        summary = ExtractionTask(tmp_project, extract_reviewer()).run(only_includes=False)
        assert summary.extracted == 1
        assert tmp_project.db.has_extraction(sid, extractor_type="ai")

    def test_batch_mode_lands_rows_and_ft_decisions(self, tmp_project):
        db = tmp_project.db
        sids = [_add_source(tmp_project, f"P{i}") for i in range(3)]
        summary = ExtractionTask(tmp_project, extract_reviewer()).run(batch=True)
        assert summary.extracted == 3
        for sid in sids:
            assert db.has_extraction(sid, extractor_type="ai")
            assert db.get_latest_ai_decision(sid, stage="full_text") is not None

    def test_clear_mock_makes_the_source_extractable_again(self, tmp_project):
        """The 0.20 real-run flow: clear mock rows first, then run — no skipped-as-done."""
        db = tmp_project.db
        sid = _add_source(tmp_project, "Candidate")
        ExtractionTask(tmp_project, extract_reviewer()).run()
        db.clear_mock_ai_extractions(tmp_project.project_id)
        assert db.has_extraction(sid, extractor_type="ai") is False
        assert db.get_latest_ai_decision(sid, stage="full_text") is None  # derived decision gone too
        rerun = ExtractionTask(tmp_project, extract_reviewer()).run()
        assert rerun.extracted == 1 and rerun.skipped_already_done == 0

    def test_extraction_rows_carry_values_and_quotes(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, "Candidate")
        ExtractionTask(tmp_project, extract_reviewer()).run()
        rows = db.list_extractions(sid, extractor_type="ai")
        field_rows = [r for r in rows if r["field_name"] != "_submitted"]
        assert field_rows
        for r in field_rows:
            assert r["value"] is not None  # every schema field got a fabricated value
        # output_format defaults to with_quotes, so the evidence has to come back with the values
        quotes = [r["source_quote"] for r in field_rows if r["source_quote"]]
        assert quotes, "with_quotes is on: at least one field must carry its supporting quote"
        assert all("Mock supporting quote" in q for q in quotes)
