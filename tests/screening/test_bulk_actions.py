"""Sources tab bulk decisions.

Bulk used to write votes with a plain INSERT, bypassing the vote lock the inline buttons go
through. On a paper someone else had already screened the row was written but ignored by every
queue and every PRISMA count — while still counting towards inter-rater agreement. These tests
pin the lock, the reporting, and idempotence.
"""

import pytest

import ailr.ui._project as ui_project
from ailr.core.config import save_stage_workflow
from ailr.core.source import Source
from ailr.reviewers import ScreeningDecision
from ailr.ui import sources_view
from tests.helpers import add_source, callbacks_of, component_text, set_config, vote


@pytest.fixture
def bulk_apply():
    """The registered bulk-decision callback, unwrapped so it can be called directly."""
    return callbacks_of(sources_view)["_bulk_apply"]


def _sources(project, n: int) -> list[int]:
    return [add_source(project, t, doi=f"10.1/{t}") for t in "ABCDEFGH"[:n]]


# ----- the lock itself -----------------------------------------------------------------------


def test_batch_lock_check_agrees_with_the_single_source_one(tmp_project):
    db = tmp_project.db
    ids = _sources(tmp_project, 4)
    vote(tmp_project.db, ids[0], "include", "amber", stage="abstract")
    vote(tmp_project.db, ids[1], "exclude", "bo", stage="abstract")
    batch = db.screening_lock_check_many(ids, "amber", "abstract")
    for sid in ids:
        assert batch[sid] == db.screening_lock_check(sid, "amber", "abstract")


def test_batch_lock_check_on_an_empty_selection(db):
    assert db.screening_lock_check_many([], "amber", "abstract") == {}


# ----- assisted: one human per paper ---------------------------------------------------------


def test_bulk_skips_papers_another_human_already_screened(tmp_project, bulk_apply):
    ids = _sources(tmp_project, 3)
    vote(tmp_project.db, ids[0], "include", "amber", stage="abstract")
    vote(tmp_project.db, ids[1], "include", "amber", stage="abstract")

    out = component_text(bulk_apply(1, [{"id": s} for s in ids], "abstract", "exclude", "not dyadic", "bo"))
    assert "Marked 1 source(s) as exclude" in out
    assert "2 skipped — already reviewed by someone else" in out

    votes = tmp_project.db.get_human_decisions_for_sources(ids, stage="abstract")
    assert [(v["reviewer_id"], v["decision"]) for v in votes[ids[0]]] == [("amber", "include")]
    assert [(v["reviewer_id"], v["decision"]) for v in votes[ids[2]]] == [("bo", "exclude")]


def test_bulk_leaves_the_other_reviewers_result_standing(tmp_project, bulk_apply):
    ids = _sources(tmp_project, 2)
    vote(tmp_project.db, ids[0], "include", "amber", stage="abstract")
    bulk_apply(1, [{"id": s} for s in ids], "abstract", "exclude", "", "bo")
    final = tmp_project.db.final_include_ids(tmp_project.project_id, "abstract", workflow="assisted")
    assert ids[0] in final          # amber's include survives the bulk exclude


def test_bulk_skips_papers_i_already_decided(tmp_project, bulk_apply):
    ids = _sources(tmp_project, 2)
    vote(tmp_project.db, ids[0], "include", "bo", stage="abstract")
    out = component_text(bulk_apply(1, [{"id": s} for s in ids], "abstract", "exclude", "", "bo"))
    assert "Marked 1 source(s)" in out
    assert "1 skipped — you had already decided them" in out


def test_running_the_same_bulk_twice_changes_nothing(tmp_project, bulk_apply):
    ids = _sources(tmp_project, 3)
    sel = [{"id": s} for s in ids]
    bulk_apply(1, sel, "abstract", "exclude", "", "bo")
    before = {s: len(tmp_project.db.get_human_decisions_for_sources([s], stage="abstract")[s]) for s in ids}

    out = component_text(bulk_apply(2, sel, "abstract", "exclude", "", "bo"))
    assert "Marked 0 source(s)" in out
    after = {s: len(tmp_project.db.get_human_decisions_for_sources([s], stage="abstract")[s]) for s in ids}
    assert after == before


def test_bulk_writes_one_audit_row_per_applied_paper(tmp_project, bulk_apply):
    ids = _sources(tmp_project, 2)
    bulk_apply(1, [{"id": s} for s in ids], "abstract", "exclude", "", "bo")
    for sid in ids:
        actions = tmp_project.db.get_screening_actions(sid)
        assert [(a["action"], a.get("decision")) for a in actions] == [("vote", "exclude")]


# ----- independent: two humans per paper -----------------------------------------------------


def test_independent_allows_a_second_human_and_that_makes_a_conflict(tmp_project, bulk_apply):
    project = set_config(tmp_project, "screening", workflow="independent")

    sid = project.db.insert_source(Source(title="A", doi="10.1/a", project_id=project.project_id))
    project.db.insert_screening_decision(ScreeningDecision(
        decision="include", reasoning="", reviewer_type="human",
        reviewer_id="amber", source_id=sid, stage="abstract",
    ))

    out = component_text(bulk_apply(1, [{"id": sid}], "abstract", "exclude", "", "bo"))
    assert "Marked 1 source(s)" in out
    assert len(project.db.list_screening_conflicts(project.project_id, stage="abstract")) == 1

    capped = component_text(bulk_apply(1, [{"id": sid}], "abstract", "exclude", "", "cy"))
    assert "Marked 0 source(s)" in capped
    assert "2 human reviewer(s) per paper" in capped


# ----- guards --------------------------------------------------------------------------------


def test_bulk_needs_a_reviewer_id(tmp_project, bulk_apply):
    assert "Set your reviewer ID" in component_text(bulk_apply(1, [{"id": 1}], "abstract", "exclude", "", "  "))


def test_bulk_needs_a_selection(tmp_project, bulk_apply):
    assert "No rows selected" in component_text(bulk_apply(1, [], "abstract", "exclude", "", "bo"))


# ----- what the lock counts ------------------------------------------------------------------


def test_an_ai_verdict_does_not_take_the_human_slot(tmp_project, bulk_apply):
    """Assisted is one human plus the AI: after an AI run the human slot is still free."""
    [sid] = _sources(tmp_project, 1)
    tmp_project.db.insert_screening_decision(ScreeningDecision(
        decision="exclude", reasoning="t", reviewer_type="ai", reviewer_id="openai:gpt",
        source_id=sid, stage="abstract",
    ))
    assert "Marked 1 source(s)" in component_text(bulk_apply(1, [{"id": sid}], "abstract", "include", "", "bo"))
    assert [d["reviewer_id"] for d in tmp_project.db.get_human_decisions(sid, "abstract")] == ["bo"]


def test_a_full_text_bulk_vote_is_counted_at_full_text_only(tmp_project, bulk_apply):
    """Someone's abstract vote neither blocks a full-text bulk vote nor receives it."""
    [sid] = _sources(tmp_project, 1)
    vote(tmp_project.db, sid, "include", "amber", stage="abstract")
    assert "Marked 1 source(s)" in component_text(bulk_apply(1, [{"id": sid}], "full_text", "exclude", "", "bo"))
    assert [d["reviewer_id"] for d in tmp_project.db.get_human_decisions(sid, "full_text")] == ["bo"]
    assert [d["reviewer_id"] for d in tmp_project.db.get_human_decisions(sid, "abstract")] == ["amber"]


def test_a_full_text_bulk_vote_follows_the_full_text_workflow(tmp_project, bulk_apply, monkeypatch):
    """Abstract assisted (one human) with full text independent (two): a second full-text vote fits."""
    save_stage_workflow(tmp_project.root, "full_text_screening", "independent")
    monkeypatch.setattr(ui_project, "_project", None)
    [sid] = _sources(tmp_project, 1)
    vote(tmp_project.db, sid, "include", "amber", stage="full_text")
    assert "Marked 1 source(s)" in component_text(bulk_apply(1, [{"id": sid}], "full_text", "exclude", "", "bo"))
