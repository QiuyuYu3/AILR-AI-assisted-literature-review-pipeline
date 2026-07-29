"""Sources tab bulk decisions.

Bulk used to write votes with a plain INSERT, bypassing the vote lock the inline buttons go
through. On a paper someone else had already screened the row was written but ignored by every
queue and every PRISMA count — while still counting towards inter-rater agreement. These tests
pin the lock, the reporting, and idempotence.
"""

import dash
import pytest
import yaml

from ailr.core.source import Source
from ailr.reviewers import ScreeningDecision
from ailr.ui import sources_view


@pytest.fixture
def bulk_apply():
    """The registered bulk-decision callback, unwrapped so it can be called directly."""
    app = dash.Dash(suppress_callback_exceptions=True)
    sources_view.register_callbacks(app)
    entry = next(
        c for key, c in app.callback_map.items()
        if "bulk-feedback.children" in key and "allow_duplicate" not in key
    )
    return entry["callback"].__wrapped__


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


def _sources(project, n: int) -> list[int]:
    return [
        project.db.insert_source(Source(title=t, doi=f"10.1/{t}", project_id=project.project_id))
        for t in "ABCDEFGH"[:n]
    ]


def _vote(project, sid: int, rid: str, decision: str, stage: str = "abstract") -> None:
    project.db.insert_screening_decision(ScreeningDecision(
        decision=decision, reasoning="", reviewer_type="human",
        reviewer_id=rid, source_id=sid, stage=stage,
    ))


def _set_workflow(project_root, value: str) -> None:
    cfg = project_root / "lit_review.yaml"
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    data.setdefault("screening", {})["workflow"] = value
    cfg.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


# ----- the lock itself -----------------------------------------------------------------------


def test_batch_lock_check_agrees_with_the_single_source_one(tmp_project):
    db = tmp_project.db
    ids = _sources(tmp_project, 4)
    _vote(tmp_project, ids[0], "amber", "include")
    _vote(tmp_project, ids[1], "bo", "exclude")
    batch = db.screening_lock_check_many(ids, "amber", "abstract")
    for sid in ids:
        assert batch[sid] == db.screening_lock_check(sid, "amber", "abstract")


def test_batch_lock_check_on_an_empty_selection(db):
    assert db.screening_lock_check_many([], "amber", "abstract") == {}


# ----- assisted: one human per paper ---------------------------------------------------------


def test_bulk_skips_papers_another_human_already_screened(tmp_project, bulk_apply):
    ids = _sources(tmp_project, 3)
    _vote(tmp_project, ids[0], "amber", "include")
    _vote(tmp_project, ids[1], "amber", "include")

    out = _text(bulk_apply(1, [{"id": s} for s in ids], "abstract", "exclude", "not dyadic", "bo"))
    assert "Marked 1 source(s) as exclude" in out
    assert "2 skipped — already reviewed by someone else" in out

    votes = tmp_project.db.get_human_decisions_for_sources(ids, stage="abstract")
    assert [(v["reviewer_id"], v["decision"]) for v in votes[ids[0]]] == [("amber", "include")]
    assert [(v["reviewer_id"], v["decision"]) for v in votes[ids[2]]] == [("bo", "exclude")]


def test_bulk_leaves_the_other_reviewers_result_standing(tmp_project, bulk_apply):
    ids = _sources(tmp_project, 2)
    _vote(tmp_project, ids[0], "amber", "include")
    bulk_apply(1, [{"id": s} for s in ids], "abstract", "exclude", "", "bo")
    final = tmp_project.db.final_include_ids(tmp_project.project_id, "abstract", workflow="assisted")
    assert ids[0] in final          # amber's include survives the bulk exclude


def test_bulk_skips_papers_i_already_decided(tmp_project, bulk_apply):
    ids = _sources(tmp_project, 2)
    _vote(tmp_project, ids[0], "bo", "include")
    out = _text(bulk_apply(1, [{"id": s} for s in ids], "abstract", "exclude", "", "bo"))
    assert "Marked 1 source(s)" in out
    assert "1 skipped — you had already decided them" in out


def test_running_the_same_bulk_twice_changes_nothing(tmp_project, bulk_apply):
    ids = _sources(tmp_project, 3)
    sel = [{"id": s} for s in ids]
    bulk_apply(1, sel, "abstract", "exclude", "", "bo")
    before = {s: len(tmp_project.db.get_human_decisions_for_sources([s], stage="abstract")[s]) for s in ids}

    out = _text(bulk_apply(2, sel, "abstract", "exclude", "", "bo"))
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


def test_independent_allows_a_second_human_and_that_makes_a_conflict(tmp_project, bulk_apply, monkeypatch):
    import ailr.ui._project as ui_project

    _set_workflow(tmp_project.root, "independent")
    monkeypatch.setattr(ui_project, "_project", None)
    project = ui_project.get_project()

    sid = project.db.insert_source(Source(title="A", doi="10.1/a", project_id=project.project_id))
    project.db.insert_screening_decision(ScreeningDecision(
        decision="include", reasoning="", reviewer_type="human",
        reviewer_id="amber", source_id=sid, stage="abstract",
    ))

    out = _text(bulk_apply(1, [{"id": sid}], "abstract", "exclude", "", "bo"))
    assert "Marked 1 source(s)" in out
    assert len(project.db.list_screening_conflicts(project.project_id, stage="abstract")) == 1

    capped = _text(bulk_apply(1, [{"id": sid}], "abstract", "exclude", "", "cy"))
    assert "Marked 0 source(s)" in capped
    assert "2 human reviewer(s) per paper" in capped


# ----- guards --------------------------------------------------------------------------------


def test_bulk_needs_a_reviewer_id(tmp_project, bulk_apply):
    assert "Set your reviewer ID" in _text(bulk_apply(1, [{"id": 1}], "abstract", "exclude", "", "  "))


def test_bulk_needs_a_selection(tmp_project, bulk_apply):
    assert "No rows selected" in _text(bulk_apply(1, [], "abstract", "exclude", "", "bo"))
