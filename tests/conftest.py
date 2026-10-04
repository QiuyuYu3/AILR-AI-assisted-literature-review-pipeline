"""Shared pytest fixtures: a throwaway project + DB, optionally seeded with sample data.

These are real (not mocked): Project.init builds a real SQLite DB with the schema, so the
tests exercise the actual project -> DB -> UI wiring. Mock only at external boundaries (LLM).
"""

import json

import pytest
from dash._callback_context import context_value
from dash._utils import AttributeDict

import ailr.ui._project as ui_project
from ailr.core.project import Project
from ailr.core.source import Source
from ailr.reviewers import ScreeningDecision


@pytest.fixture
def tmp_project(tmp_path, monkeypatch):
    """A real but empty project wired up as the active UI project (AILR_PROJECT)."""
    root = tmp_path / "proj"
    project = Project.init(root)
    monkeypatch.setenv("AILR_PROJECT", str(root))
    monkeypatch.setattr(ui_project, "_project", None)              # reset get_project() cache
    monkeypatch.setattr(ui_project, "_RECENT_FILE", tmp_path / "recent.json")  # keep ~/.ailr untouched
    yield project
    # an open SQLite file keeps Windows from deleting the folder; close the copy the UI loaded too
    for p in {id(x): x for x in (project, ui_project._project) if x is not None}.values():
        p.db.close()


@pytest.fixture
def click():
    """Fake the Dash context of one click on a component; reset afterwards so it cannot leak."""
    tokens = []

    def _click(component_id, prop="n_clicks"):
        cid = json.dumps(component_id) if isinstance(component_id, dict) else component_id
        tokens.append(context_value.set(AttributeDict(triggered_inputs=[{"prop_id": f"{cid}.{prop}", "value": 1}])))

    yield _click
    for token in reversed(tokens):
        context_value.reset(token)


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
