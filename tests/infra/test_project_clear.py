"""Clearing a project's data. The amendment log is derived from artifact_versions, so a clear
that left those rows behind gave a re-imported review the previous review's protocol history.
"""

import pytest

from ailr.core._db_schema import metadata
from ailr.core.crosscheck import CrossCheckRecord
from ailr.core.source import Source
from ailr.llm.base import CallMetadata
from ailr.reviewers import ExtractionResult, ScreeningDecision
from tests.helpers import vote


def _seed(project):
    db = project.db
    pid = project.project_id
    sid = db.insert_source(Source(title="Paper", project_id=pid))
    db.insert_screening_decision(ScreeningDecision(
        decision="include", reasoning="fits", reviewer_type="human",
        reviewer_id="amber", source_id=sid, stage="abstract",
    ))
    db.save_artifact_version(pid, "criteria", '{"criteria": []}', "protocol as written")
    db.save_artifact_version(pid, "criteria", '{"criteria": [{"id": "c1"}]}', "amendment")
    db.save_prompt_version(pid, "screening", "decide include/exclude", "v1")
    return sid


class TestDeleteProjectData:
    def test_artifact_history_does_not_survive_a_clear(self, tmp_project):
        db = tmp_project.db
        pid = tmp_project.project_id
        _seed(tmp_project)
        assert len(db.list_artifact_versions(pid, "criteria")) == 2

        db.delete_project_data(pid)

        assert db.list_artifact_versions(pid, "criteria") == []
        assert db.latest_prompt_version(pid, "screening") is None

    def test_the_review_data_goes_and_the_project_row_stays(self, tmp_project):
        db = tmp_project.db
        pid = tmp_project.project_id
        _seed(tmp_project)

        db.delete_project_data(pid)

        assert db.count_sources(pid) == 0
        assert db.count_screening_decisions(pid, reviewer_type="human") == 0
        assert db.get_or_create_project(tmp_project.config.project.name) == pid


def _seed_every_table(db, pid):
    """One row of this project's data in every table that holds any."""
    sid = db.insert_source(Source(title=f"Paper of project {pid}", project_id=pid))
    vote(db, sid, "include", "amber", stage="abstract")
    db.insert_extraction(ExtractionResult(
        extractor_type="ai", extractor_id="gpt", field_name="design", value="obs", source_id=sid))
    db.replace_cross_checks(sid, "extraction", "ai", "gpt", "deterministic", [CrossCheckRecord(
        source_id=sid, stage="extraction", target_type="ai", target_id="gpt", checker_type="ai",
        checker_id="ailr:deterministic", check_kind="deterministic", verdict="disagree", field_name="design")])
    db.save_prompt_version(pid, "screening", "decide include/exclude", "v1")
    db.save_artifact_version(pid, "criteria", '{"criteria": []}', "as written")
    db._conn.execute("INSERT INTO codebook_versions (project_id, version, content) VALUES (?, 'v1', '{}')", (pid,))
    db._conn.commit()
    db.insert_screening_reconciliation(sid, "include", "pi", "agreed", stage="abstract")
    db.tag_source(sid, db.create_tag(pid, "to-revisit"))
    db.insert_screening_action(sid, "amber", action="vote", decision="include")
    db.add_note(sid, "amber", "check the supplement")
    db.insert_duplicate(pid, "a dropped copy", None, "doi")
    db.create_exclusion_reason(pid, "Wrong population")
    db.create_calibration_sample(pid, [sid], "screening", 1)
    db.insert_api_call(pid, CallMetadata(provider="anthropic", model="claude-x", input_tokens=10, output_tokens=5))
    run_id = db.create_test_run(project_id=pid, stage="abstract", sample_size=1, prompt_snapshot="p", criteria_snapshot="c")
    db.insert_test_decision(run_id, sid, "include", "r", 0.9, [], [])
    ft_run = db.create_test_run(project_id=pid, stage="extraction", sample_size=1, prompt_snapshot="p", criteria_snapshot="c")
    db.insert_test_extraction(ft_run, sid, "include", [], None)
    db.add_search_strategy(pid, "PubMed", "dyad*", "2026-01-01", None, 10, 9)


def _row_counts(db) -> dict[str, int]:
    return {t: db._conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"] for t in metadata.tables}


class TestClearingOneProjectOfSeveral:
    """Projects can share one database, so a clear must empty every table for this project only."""

    @pytest.mark.parametrize("keep_project_row", [True, False])
    def test_every_table_is_emptied_for_this_project_and_untouched_for_the_other(self, tmp_project, keep_project_row):
        db, pid = tmp_project.db, tmp_project.project_id
        _seed_every_table(db, db.get_or_create_project("another review"))
        theirs = _row_counts(db)
        _seed_every_table(db, pid)
        seeded = _row_counts(db)
        # the seed reaches every table, so a table added later without a delete step shows up here
        assert {t for t in seeded if seeded[t] > theirs[t]} == set(metadata.tables) - {"projects"}

        db.delete_project_data(pid, keep_project_row=keep_project_row)

        expected = dict(theirs, projects=theirs["projects"] - (0 if keep_project_row else 1))
        assert _row_counts(db) == expected
