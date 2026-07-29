"""CLI surface against a real project folder: the `workflow` command (print / set / reject),
and `show disagreements`, whose --stage must not leak decisions across stages.

The CLI is the scripting path into the same config writers and queries the UI uses, so these
guard the argument validation the UI never exercises.
"""

import json

from typer.testing import CliRunner

from ailr.cli import app
from ailr.core.config import save_stage_workflow
from ailr.core.project import Project
from ailr.core.source import Source
from ailr.reviewers import ScreeningDecision

runner = CliRunner()


def _run(*args):
    return runner.invoke(app, list(args))


def _vote(db, source_id, decision, reviewer_id, reviewer_type, stage):
    db.insert_screening_decision(ScreeningDecision(
        decision=decision, reasoning="test", reviewer_type=reviewer_type,
        reviewer_id=reviewer_id, source_id=source_id, stage=stage,
    ))


class TestWorkflowCommand:
    def test_prints_all_three_stages(self, tmp_project):
        save_stage_workflow(tmp_project.root, "screening", "assisted")
        save_stage_workflow(tmp_project.root, "full_text_screening", "independent")
        save_stage_workflow(tmp_project.root, "extraction", "verify")

        result = _run("workflow", str(tmp_project.root))

        assert result.exit_code == 0
        assert "abstract" in result.stdout and "assisted" in result.stdout
        assert "full-text" in result.stdout and "independent" in result.stdout
        assert "extraction" in result.stdout and "verify" in result.stdout

    def test_set_writes_the_full_text_override_only(self, tmp_project):
        save_stage_workflow(tmp_project.root, "screening", "assisted")

        result = _run("workflow", str(tmp_project.root), "--stage", "full-text", "--set", "independent")

        assert result.exit_code == 0
        cfg = Project(tmp_project.root).config
        assert cfg.screening_workflow("full_text") == "independent"
        assert cfg.screening_workflow("abstract") == "assisted"

    def test_set_extraction_workflow(self, tmp_project):
        result = _run("workflow", str(tmp_project.root), "--stage", "extraction", "--set", "independent")

        assert result.exit_code == 0
        assert Project(tmp_project.root).config.extraction.workflow == "independent"

    def test_unknown_stage_is_rejected(self, tmp_project):
        result = _run("workflow", str(tmp_project.root), "--stage", "screening")
        assert result.exit_code == 1
        assert "--stage must be one of" in result.stderr  # our validation, not a crash

    def test_set_without_stage_is_rejected(self, tmp_project):
        result = _run("workflow", str(tmp_project.root), "--set", "independent")
        assert result.exit_code == 1
        assert "--set needs --stage" in result.stderr

    def test_value_from_the_wrong_stage_is_rejected(self, tmp_project):
        """`verify` is an extraction workflow; it must not be writable to a screening stage."""
        result = _run("workflow", str(tmp_project.root), "--stage", "abstract", "--set", "verify")

        assert result.exit_code == 1
        assert "--set must be one of" in result.stderr
        assert Project(tmp_project.root).config.screening_workflow("abstract") != "verify"


class TestShowDisagreements:
    def test_unknown_stage_is_rejected(self, tmp_project):
        result = _run("show", "disagreements", str(tmp_project.root), "--stage", "abstracts")
        assert result.exit_code == 1
        assert "--stage must be 'abstract' or 'full_text'" in result.stderr

    def test_only_the_requested_stage_is_reported(self, tmp_project):
        db = tmp_project.db
        sid = db.insert_source(Source(title="Paper", project_id=tmp_project.project_id))
        _vote(db, sid, "include", "mock:mock", "ai", "abstract")
        _vote(db, sid, "exclude", "amber", "human", "abstract")
        _vote(db, sid, "include", "mock:mock", "ai", "full_text")
        _vote(db, sid, "include", "amber", "human", "full_text")

        abstract = _run("show", "disagreements", str(tmp_project.root), "--stage", "abstract", "--json")
        full_text = _run("show", "disagreements", str(tmp_project.root), "--stage", "full_text", "--json")

        assert abstract.exit_code == 0 and full_text.exit_code == 0
        assert json.loads(abstract.stdout)[0]["source_id"] == sid
        assert json.loads(full_text.stdout) == []

    def test_defaults_to_the_abstract_stage(self, tmp_project):
        db = tmp_project.db
        sid = db.insert_source(Source(title="Paper", project_id=tmp_project.project_id))
        _vote(db, sid, "include", "mock:mock", "ai", "abstract")
        _vote(db, sid, "exclude", "amber", "human", "abstract")

        result = _run("show", "disagreements", str(tmp_project.root))

        assert result.exit_code == 0
        assert "1 disagreement(s) at the abstract stage" in result.stdout
