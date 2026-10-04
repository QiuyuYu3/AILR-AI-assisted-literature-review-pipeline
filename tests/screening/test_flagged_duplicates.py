"""A record flagged as a duplicate has left the review, as PRISMA counts it: no queue, count or run holds it."""

import pytest

from ailr.ui.dashboard_view import _build_content
from tests.helpers import add_source, component_text, set_config, vote


def _screened(project):
    """Three screened papers, the third later flagged as a duplicate, plus a copy flagged before screening."""
    db = project.db
    agreed = add_source(project, "agreed")
    disputed = add_source(project, "disputed")
    flagged = add_source(project, "screened, then flagged as a duplicate")
    copy = add_source(project, "flagged before anyone screened it")
    for sid, human in ((agreed, "include"), (disputed, "exclude"), (flagged, "exclude")):
        vote(db, sid, human, "amber", stage="abstract")
        vote(db, sid, "include", "gpt", stage="abstract", reviewer_type="ai")
    for sid in (flagged, copy):
        db.mark_source_duplicate(sid, True)
    return agreed, disputed, flagged


def test_the_dashboard_counts_leave_them_out(tmp_project):
    _screened(tmp_project)
    text = component_text(_build_content("amber"))
    for expected in ("2 sources in database", "2 human decisions", "across 2 unique source(s)",
                     "AI: 2 decisions", "You: 2 / 2 reviewed", "1 unresolved", "abstract: 1"):
        assert expected in text, expected


@pytest.mark.parametrize("workflow", ["assisted", "independent"])
def test_a_flagged_duplicate_leaves_the_conflicts_queue(tmp_project, workflow):
    project = set_config(tmp_project, "screening", workflow=workflow)
    db, pid = project.db, project.project_id
    disputed, flagged = add_source(project, "disputed"), add_source(project, "flagged")
    for sid in (disputed, flagged):
        vote(db, sid, "include", "amber", stage="abstract")
        vote(db, sid, "exclude", "bo" if workflow == "independent" else "gpt", stage="abstract",
             reviewer_type="human" if workflow == "independent" else "ai")
    db.mark_source_duplicate(flagged, True)
    listed = db.list_screening_conflicts(pid) if workflow == "independent" else db.list_assisted_conflicts(pid)
    assert [s.id for s in listed] == [disputed]
    assert db.unresolved_conflict_ids(pid, workflow) == {disputed}


def test_the_helper_queries_leave_them_out(tmp_project):
    db, pid = tmp_project.db, tmp_project.project_id
    agreed, disputed, flagged = _screened(tmp_project)
    assert db.count_reviewer_decisions(pid, "amber") == 2
    assert db.source_ids_with_decisions(pid, "abstract", ["ai"]) == [agreed, disputed]

    ft = [add_source(tmp_project, title, with_md=True) for title in ("included", "included, then flagged")]
    for sid in ft:
        vote(db, sid, "include", "amber", stage="full_text")
    db.mark_source_duplicate(ft[1], True)
    assert [s.id for s in db.list_full_text_final_includes_with_markdown(pid)] == [ft[0]]
    assert db.count_sources_with_markdown(pid) == 1
