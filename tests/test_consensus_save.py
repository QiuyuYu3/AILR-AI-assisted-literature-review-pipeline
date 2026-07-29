"""Consensus save: the two halves of the record must come from the same moment.

Fields the reviewers agree on are read fresh at save time; the answers picked for the fields they
disagree on come from the store written when the page rendered. If someone re-submits their
extraction in between, mixing the two writes a consensus record that matches neither reviewer.
"""

import dash
import pytest
import yaml
from dash._callback_context import context_value
from dash._utils import AttributeDict

from ailr.core.source import Source
from ailr.reviewers import ExtractionResult
from ailr.ui import consensus_view
from ailr.ui.consensus_view import _compare, _shape

_FIELD = "primary_research_goal"   # a plain string field in the default schema


@pytest.fixture
def project(tmp_project, monkeypatch):
    """tmp_project switched to independent extraction, which is what consensus exists for."""
    import ailr.ui._project as ui_project

    cfg = tmp_project.root / "lit_review.yaml"
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    data.setdefault("extraction", {})["workflow"] = "independent"
    cfg.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(ui_project, "_project", None)
    return ui_project.get_project()


@pytest.fixture
def save():
    """The consensus save callback, with a faked callback context (it reads ctx.triggered_id)."""
    app = dash.Dash(suppress_callback_exceptions=True)
    consensus_view.register_callbacks(app)
    fn = next(v["callback"] for k, v in app.callback_map.items() if "cons-feedback.children" in k).__wrapped__

    def call(*args, trigger="cons-save"):
        context_value.set(AttributeDict(triggered_inputs=[{"prop_id": f"{trigger}.n_clicks", "value": 1}]))
        return fn(*args)

    return call


def _submit(project, sid: int, rid: str, value: str) -> None:
    project.db.insert_extractions([ExtractionResult(
        extractor_type="human", extractor_id=rid, source_id=sid,
        field_name=_FIELD, value=value, prompt_version="manual",
    )])
    project.db.mark_extraction_submitted(sid, rid)


@pytest.fixture
def disagreeing(project):
    sid = project.db.insert_source(Source(title="A", doi="10.1/a", project_id=project.project_id))
    _submit(project, sid, "amber", "synchrony")
    _submit(project, sid, "bo", "turn-taking")
    return sid


def _text(component) -> str:
    out: list[str] = []

    def walk(x):
        if isinstance(x, str):
            out.append(x)
        children = getattr(x, "children", None)
        for y in (children if isinstance(children, (list, tuple)) else [children] if children is not None else []):
            walk(y)

    walk(component)
    return " ".join(out)


def _consensus(project, sid: int) -> dict:
    return {r["field_name"]: r["value"] for r in project.db.list_extractions(sid, extractor_type="consensus")}


def test_shape_lists_the_distinct_answers_per_disagreeing_field(project, disagreeing):
    _agreed, _cards, state = _compare(project, disagreeing)
    assert _shape(state) == {_FIELD: ["synchrony", "turn-taking"]}


def test_shape_of_nothing(project):
    assert _shape(None) == {} and _shape({}) == {}


def test_save_records_the_picked_answer(project, disagreeing, save):
    _agreed, _cards, state = _compare(project, disagreeing)
    picked = "synchrony"
    _fb, _refresh, tab = save(1, None, {"sid": disagreeing}, state, "QY",
                              [picked], [{"field": _FIELD}], [], [], [], [])
    assert tab == "full_text"
    assert _consensus(project, disagreeing)[_FIELD] == "synchrony"


def test_save_is_refused_when_a_reviewer_resubmitted_meanwhile(project, disagreeing, save):
    _agreed, _cards, state = _compare(project, disagreeing)
    picked = "synchrony"
    _submit(project, disagreeing, "bo", "quasi-experimental")   # bo changes their mind

    fb, refresh, tab = save(1, None, {"sid": disagreeing}, state, "QY",
                            [picked], [{"field": _FIELD}], [], [], [], [])
    assert "changed since you opened it" in _text(fb)
    assert refresh                      # the comparison is re-rendered with the new answers
    assert tab != "full_text"           # you stay on the page
    assert _consensus(project, disagreeing) == {}   # nothing was written


def test_save_succeeds_once_the_comparison_is_refreshed(project, disagreeing, save):
    _submit(project, disagreeing, "bo", "quasi-experimental")
    _agreed, _cards, fresh = _compare(project, disagreeing)
    picked = sorted(fresh[_FIELD])[0]
    _fb, _refresh, tab = save(2, None, {"sid": disagreeing}, fresh, "QY",
                              [picked], [{"field": _FIELD}], [], [], [], [])
    assert tab == "full_text"
    assert _consensus(project, disagreeing)[_FIELD] == picked


def test_save_needs_a_reviewer_id(project, disagreeing, save):
    _agreed, _cards, state = _compare(project, disagreeing)
    fb, _r, _t = save(1, None, {"sid": disagreeing}, state, "  ",
                      ["synchrony"], [{"field": _FIELD}], [], [], [], [])
    assert "reviewer ID" in _text(fb)
    assert _consensus(project, disagreeing) == {}


def test_save_refuses_while_a_field_is_undecided(project, disagreeing, save):
    _agreed, _cards, state = _compare(project, disagreeing)
    fb, _r, _t = save(1, None, {"sid": disagreeing}, state, "QY",
                      [None], [{"field": _FIELD}], [], [], [], [])
    assert "Still undecided" in _text(fb)
    assert _consensus(project, disagreeing) == {}
