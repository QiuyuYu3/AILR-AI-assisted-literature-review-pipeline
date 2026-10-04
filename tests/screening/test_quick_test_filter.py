"""The queues' 'Last quick test' status filter.

It used to list the full-calibration sample, which nothing in the UI writes any more. It now
lists the papers the most recent quick-test run covered, which is a different table per stage
(test_decisions for abstract, test_extractions for full text) under a third stage vocabulary
('abstract' / 'extraction').
"""

from pathlib import Path

from ailr.core.source import Source
from tests.helpers import add_source, settle


def _run(db, project, stage, sample_size):
    return db.create_test_run(
        project_id=project.project_id, stage=stage, sample_size=sample_size,
        prompt_snapshot="p", criteria_snapshot="c",
    )


def _abstract_page(db, pid, status="quick_test"):
    rows, _total, _page = db.list_sources_page(pid, "amber", status=status, page_size=100)
    return {s.id for s in rows}


def test_abstract_queue_lists_the_latest_quick_test_only(tmp_project):
    db = tmp_project.db
    old = [add_source(tmp_project, f"old {i}") for i in range(2)]
    new = [add_source(tmp_project, f"new {i}") for i in range(3)]
    add_source(tmp_project, "never tested")

    old_run = _run(db, tmp_project, "abstract", len(old))
    for sid in old:
        db.insert_test_decision(old_run, sid, "include", "r", 0.9, [], [])
    new_run = _run(db, tmp_project, "abstract", len(new))
    for sid in new:
        db.insert_test_decision(new_run, sid, "exclude", "r", 0.9, [], [])

    assert _abstract_page(db, tmp_project.project_id) == set(new)


def test_the_old_filter_value_still_works_from_a_saved_session(tmp_project):
    db = tmp_project.db
    sid = add_source(tmp_project, "tested")
    run_id = _run(db, tmp_project, "abstract", 1)
    db.insert_test_decision(run_id, sid, "include", "r", 0.9, [], [])

    assert _abstract_page(db, tmp_project.project_id, status="calibration") == {sid}


def test_no_quick_test_yet_matches_nothing(tmp_project):
    add_source(tmp_project, "untested")
    assert _abstract_page(tmp_project.db, tmp_project.project_id) == set()


def test_an_extraction_run_does_not_leak_into_the_abstract_queue(tmp_project):
    """The two stages write different tables under different stage names; reading the wrong
    pair is the mistake this filter is one rename away from."""
    db = tmp_project.db
    sid = add_source(tmp_project, "full-text tested")
    run_id = _run(db, tmp_project, "extraction", 1)
    db.insert_test_extraction(run_id, sid, "include", [], None)

    assert _abstract_page(db, tmp_project.project_id) == set()


def test_full_text_queue_lists_the_latest_quick_test_only(tmp_project):
    db = tmp_project.db
    pid = tmp_project.project_id
    sids = []
    for i in range(3):
        sid = add_source(tmp_project, f"ft {i}")
        db.update_markdown_path(sid, Path("data/markdown") / f"{sid}.md")
        settle(db, sid, "include", stage="abstract")
        sids.append(sid)

    old_run = _run(db, tmp_project, "extraction", 1)
    db.insert_test_extraction(old_run, sids[0], "include", [], None)
    new_run = _run(db, tmp_project, "extraction", 2)
    for sid in sids[1:]:
        db.insert_test_extraction(new_run, sid, "include", [], None)

    rows, _total, _page = db.list_full_text_page(
        pid, "amber", status="quick_test", team_size=1, extractors_required=1,
        abstract_workflow="assisted", page_size=100,
    )
    assert {s.id for s in rows} == set(sids[1:])


def test_a_later_full_text_run_does_not_replace_the_abstract_one(tmp_project):
    db = tmp_project.db
    sid = add_source(tmp_project, "abstract tested")
    run_id = _run(db, tmp_project, "abstract", 1)
    db.insert_test_decision(run_id, sid, "include", "r", 0.9, [], [])
    ft_run = _run(db, tmp_project, "extraction", 1)
    db.insert_test_extraction(ft_run, add_source(tmp_project, "full-text tested"), "include", [], None)

    assert _abstract_page(db, tmp_project.project_id) == {sid}


def test_another_projects_later_run_does_not_replace_this_ones(tmp_project):
    """Projects can share one Postgres database, so the latest run must be this project's own."""
    db = tmp_project.db
    sid = add_source(tmp_project, "ours")
    run_id = _run(db, tmp_project, "abstract", 1)
    db.insert_test_decision(run_id, sid, "include", "r", 0.9, [], [])
    other_pid = db.get_or_create_project("another review")
    theirs = db.insert_source(Source(title="theirs", project_id=other_pid))
    their_run = db.create_test_run(project_id=other_pid, stage="abstract", sample_size=1,
                                   prompt_snapshot="p", criteria_snapshot="c")
    db.insert_test_decision(their_run, theirs, "include", "r", 0.9, [], [])

    assert _abstract_page(db, tmp_project.project_id) == {sid}
