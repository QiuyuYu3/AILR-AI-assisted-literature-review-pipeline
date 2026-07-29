"""Screen-tab callback logic without a browser.

Two halves:
1. triggered_click_id() against a mocked Dash callback context — including the regression
   for the historical bug where ctx.triggered_id pointed at a value-less freshly-rendered
   button and the vote landed on the wrong paper.
2. _apply_vote / _apply_reset (the extracted body of the Screen tab's action callback)
   against a real project DB: vote lock, team-size cap per workflow, reset semantics.
"""

import json
from contextvars import copy_context

from dash import no_update
from dash._callback_context import context_value
from dash._utils import AttributeDict

from ailr.core.source import Source
from ailr.reviewers import ScreeningDecision
from ailr.ui._actions import _apply_reset, _apply_vote
from ailr.ui._common import triggered_click_id

# ---------- half 1: triggered_click_id ----------

def _decide_id(source_id, decision="include"):
    return {"type": "screen-decide", "source": source_id, "decision": decision}


def _entry(component_id, value):
    """One ctx.triggered entry as Dash builds it: '<stringified id>.<prop>' + the new value."""
    prop = json.dumps(component_id, sort_keys=True, separators=(",", ":")) if isinstance(component_id, dict) else component_id
    return {"prop_id": f"{prop}.n_clicks", "value": value}


def _run_triggered_click_id(triggered):
    def inner():
        context_value.set(AttributeDict(triggered_inputs=triggered))
        return triggered_click_id()
    return copy_context().run(inner)


def test_single_real_click_is_returned():
    got = _run_triggered_click_id([_entry(_decide_id(5), 1)])
    assert got == _decide_id(5)


def test_no_click_value_returns_none():
    # freshly rendered buttons fire with n_clicks=None -> not a real click
    got = _run_triggered_click_id([_entry(_decide_id(5), None)])
    assert got is None


def test_empty_triggered_returns_none():
    assert _run_triggered_click_id([]) is None


def test_regression_stale_first_trigger_does_not_steal_the_click():
    """The serial bug: card list re-renders while the user clicks, so ctx.triggered lists a
    value-less button FIRST. The vote must go to the button that actually carries the click."""
    got = _run_triggered_click_id([
        _entry(_decide_id(99), None),   # freshly-rendered button, no real click
        _entry(_decide_id(5), 1),       # the paper the user actually clicked
    ])
    assert got == _decide_id(5)


def test_non_pattern_ids_are_ignored():
    got = _run_triggered_click_id([
        _entry("screen-refresh", 123),  # plain-string id with a value -> not a card button
        _entry(_decide_id(7), 1),
    ])
    assert got == _decide_id(7)


# ---------- half 2: _apply_vote / _apply_reset ----------

def _add_source(project, title="Paper"):
    return project.db.insert_source(Source(title=title, project_id=project.project_id))


def _decisions(db, sid):
    return db.get_human_decisions(sid, "abstract")


def test_vote_records_decision_and_action(tmp_project):
    db = tmp_project.db
    sid = _add_source(tmp_project, title="Gaze study")
    refresh, last = _apply_vote(db, sid, "include", "amber", "assisted")
    assert refresh and "ts" in refresh
    assert last["sid"] == sid and last["decision"] == "include"
    rows = _decisions(db, sid)
    assert len(rows) == 1 and rows[0]["reviewer_id"] == "amber" and rows[0]["decision"] == "include"
    actions = db.get_screening_actions(sid)
    assert [(a["action"], a["decision"]) for a in actions] == [("vote", "include")]


def test_double_click_is_idempotent(tmp_project):
    db = tmp_project.db
    sid = _add_source(tmp_project)
    _apply_vote(db, sid, "include", "amber", "assisted")
    refresh, last = _apply_vote(db, sid, "include", "amber", "assisted")
    assert last is no_update           # second click quietly skipped
    assert refresh and "ts" in refresh  # but the UI still refreshes
    assert len(_decisions(db, sid)) == 1


def test_assisted_blocks_a_second_human(tmp_project):
    db = tmp_project.db
    sid = _add_source(tmp_project)
    _apply_vote(db, sid, "include", "amber", "assisted")
    _, last = _apply_vote(db, sid, "exclude", "bob", "assisted")
    assert last["blocked"] is True and last["by"] == "amber" and last["sid"] == sid
    reviewers = [r["reviewer_id"] for r in _decisions(db, sid)]
    assert reviewers == ["amber"]  # bob's vote was not recorded


def test_independent_allows_two_humans_blocks_third(tmp_project):
    db = tmp_project.db
    sid = _add_source(tmp_project)
    _apply_vote(db, sid, "include", "amber", "independent")
    _, last_bob = _apply_vote(db, sid, "exclude", "bob", "independent")
    assert last_bob["decision"] == "exclude"  # recorded, not blocked and not silently skipped
    _, last_carol = _apply_vote(db, sid, "include", "carol", "independent")
    assert last_carol["blocked"] is True
    reviewers = {r["reviewer_id"] for r in _decisions(db, sid)}
    assert reviewers == {"amber", "bob"}


def test_reset_clears_vote_and_final_decision(tmp_project):
    db = tmp_project.db
    sid = _add_source(tmp_project)
    _apply_vote(db, sid, "exclude", "amber", "assisted")
    db.insert_screening_reconciliation(sid, "exclude", adjudicator="amber", stage="abstract")
    refresh, last = _apply_reset(db, sid, "amber")
    assert refresh and "ts" in refresh
    assert last is None  # banner cleared
    assert _decisions(db, sid) == []
    # Query the stage the reconciliation was actually written under. The old helper looked for
    # stage='screening', which is never written, so this assertion passed even when reset failed.
    assert db.list_reconciliations(tmp_project.project_id, stage="abstract_screening") == []
    # the paper is votable again
    _, again = _apply_vote(db, sid, "include", "amber", "assisted")
    assert again["decision"] == "include"


def test_reset_only_removes_my_vote(tmp_project):
    db = tmp_project.db
    sid = _add_source(tmp_project)
    _apply_vote(db, sid, "include", "amber", "independent")
    _apply_vote(db, sid, "exclude", "bob", "independent")
    _apply_reset(db, sid, "amber")
    reviewers = [r["reviewer_id"] for r in _decisions(db, sid)]
    assert reviewers == ["bob"]


def test_ai_decision_does_not_block_the_human(tmp_project):
    db = tmp_project.db
    sid = _add_source(tmp_project)
    db.insert_screening_decision(ScreeningDecision(
        decision="exclude", reasoning="ai says no", reviewer_type="ai",
        reviewer_id="gpt", source_id=sid, stage="abstract",
    ))
    _, last = _apply_vote(db, sid, "include", "amber", "assisted")
    assert last["decision"] == "include"  # the AI never consumes the human slot
