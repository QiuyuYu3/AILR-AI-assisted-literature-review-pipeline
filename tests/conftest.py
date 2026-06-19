"""Shared pytest fixtures: a throwaway project + DB, optionally seeded with sample data.

These are real (not mocked): Project.init builds a real SQLite DB with the schema, so the
tests exercise the actual project -> DB -> UI wiring. Mock only at external boundaries (LLM).
"""

import pytest

import ailr.ui._common as common
from ailr.core.project import Project
from ailr.core.source import Source
from ailr.reviewers import ScreeningDecision


@pytest.fixture
def tmp_project(tmp_path, monkeypatch):
    """A real but empty project wired up as the active UI project (AILR_PROJECT)."""
    root = tmp_path / "proj"
    project = Project.init(root)
    monkeypatch.setenv("AILR_PROJECT", str(root))
    monkeypatch.setattr(common, "_project", None)              # reset get_project() cache
    monkeypatch.setattr(common, "_RECENT_FILE", tmp_path / "recent.json")  # keep ~/.ailr untouched
    return project


@pytest.fixture
def db(tmp_project):
    """The project's Database, for DB-only tests."""
    return tmp_project.db


@pytest.fixture
def seeded_project(tmp_project):
    """tmp_project with a little sample data so non-empty render paths get exercised."""
    p = tmp_project
    sid = p.db.insert_source(Source(
        title="Dyadic gaze in infancy", doi="10.1/x", year=2021,
        authors=["Lee, J", "Park, S"], project_id=p.project_id,
    ))
    p.db.insert_screening_decision(ScreeningDecision(
        decision="include", reasoning="fits", reviewer_type="human",
        reviewer_id="amber", source_id=sid, stage="abstract",
    ))
    tag_id = p.db.create_tag(p.project_id, "to-revisit")
    p.db.tag_source(sid, tag_id)
    return p
