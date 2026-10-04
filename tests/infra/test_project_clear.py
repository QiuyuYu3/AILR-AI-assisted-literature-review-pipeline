"""Clearing a project's data. The amendment log is derived from artifact_versions, so a clear
that left those rows behind gave a re-imported review the previous review's protocol history.
"""

from ailr.core.source import Source
from ailr.reviewers import ScreeningDecision


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
