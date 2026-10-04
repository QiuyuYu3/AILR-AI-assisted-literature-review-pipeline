"""Consensus save: the two halves of the record must come from the same moment.

Fields the reviewers agree on are read fresh at save time; the answers picked for the fields they
disagree on come from the store written when the page rendered. If someone re-submits their
extraction in between, mixing the two writes a consensus record that matches neither reviewer.
"""

import pytest

from ailr.core.source import Source
from ailr.reviewers import ExtractionResult
from ailr.ui import consensus_view
from ailr.ui.consensus_view import _compare, _shape
from tests.helpers import callbacks_of, component_text, set_config

_FIELD = "primary_research_goal"   # a plain string field in the default schema


@pytest.fixture
def project(tmp_project):
    """tmp_project switched to independent extraction, which is what consensus exists for."""
    return set_config(tmp_project, "extraction", workflow="independent")


@pytest.fixture
def save(click):
    """The consensus save callback, with a faked callback context (it reads ctx.triggered_id)."""
    fn = callbacks_of(consensus_view)["_save"]

    def call(*args, trigger="cons-save"):
        click(trigger)
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


def test_save_writes_the_answer_the_pick_stands_for(project, save):
    """The radio hands back a text key. The record must get the answer behind it: an integer stays
    an integer, and the quote and the adjudicator come along."""
    sid = project.db.insert_source(Source(title="B", doi="10.1/b", project_id=project.project_id))
    for rid, n, quote in (("amber", 24, "24 dyads took part"), ("bo", 30, "thirty dyads")):
        project.db.insert_extractions([ExtractionResult(
            extractor_type="human", extractor_id=rid, source_id=sid, field_name="sample_size",
            value=n, source_quote=quote, prompt_version="manual",
        )])
        project.db.mark_extraction_submitted(sid, rid)
    _agreed, _cards, state = _compare(project, sid)
    key = next(k for k, answer in state["sample_size"].items() if answer["value"] == 24)

    save(1, None, {"sid": sid}, state, "QY", [key], [{"field": "sample_size"}], [], [], [], [])
    [row] = [r for r in project.db.list_extractions(sid, extractor_type="consensus") if r["field_name"] == "sample_size"]
    assert row["value"] == 24 and isinstance(row["value"], int)
    assert (row["source_quote"], row["extractor_id"]) == ("24 dyads took part", "QY")


def test_save_is_refused_when_a_reviewer_resubmitted_meanwhile(project, disagreeing, save):
    _agreed, _cards, state = _compare(project, disagreeing)
    picked = "synchrony"
    _submit(project, disagreeing, "bo", "quasi-experimental")   # bo changes their mind

    fb, refresh, tab = save(1, None, {"sid": disagreeing}, state, "QY",
                            [picked], [{"field": _FIELD}], [], [], [], [])
    assert "changed since you opened it" in component_text(fb)
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
    assert "reviewer ID" in component_text(fb)
    assert _consensus(project, disagreeing) == {}


def test_save_refuses_while_a_field_is_undecided(project, disagreeing, save):
    _agreed, _cards, state = _compare(project, disagreeing)
    fb, _r, _t = save(1, None, {"sid": disagreeing}, state, "QY",
                      [None], [{"field": _FIELD}], [], [], [], [])
    assert "Still undecided" in component_text(fb)
    assert _consensus(project, disagreeing) == {}
