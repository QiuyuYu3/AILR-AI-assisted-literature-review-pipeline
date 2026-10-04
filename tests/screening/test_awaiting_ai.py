"""Assisted mode: a paper the AI has not judged yet is not settled, and it is not a conflict either."""

import json

from ailr.core.config import save_stage_workflow
from ailr.core.project import Project
from ailr.exports.prisma import prisma_counts
from ailr.reviewers import ExtractionResult
from ailr.ui import extract_view, full_text_view
from ailr.ui.dashboard_view import _build_content
from tests.helpers import add_source, callbacks_of, component_text, settle, vote, walk


def _state(project, stage="abstract"):
    db, pid = project.db, project.project_id
    workflow = project.config.screening_workflow(stage)
    return {
        "included": db.final_include_ids(pid, stage, workflow=workflow),
        "excluded": db.final_exclude_ids(pid, stage, workflow=workflow),
        "conflicts": db.unresolved_conflict_ids(pid, workflow, stage=stage),
        "awaiting": db.awaiting_ai_ids(pid, workflow, stage=stage),
    }


def _ft_render(status):
    render = callbacks_of(full_text_view)["_render"]
    cards, *_ = render(status, None, "amber", None, None, "", "title_and_abstract", None, None, "id", 50, None, None)
    return cards


def _ft_listed(status):
    return {n.id["source"] for n in walk(_ft_render(status))
            if isinstance(getattr(n, "id", None), dict) and "source" in n.id}


class TestAbstract:
    def test_a_human_vote_alone_settles_nothing(self, tmp_project):
        db = tmp_project.db
        ids = {d: add_source(tmp_project, f"human {d}") for d in ("include", "exclude", "uncertain")}
        for decision, sid in ids.items():
            vote(db, sid, decision, "amber", stage="abstract")
        assert _state(tmp_project) == {
            "included": set(), "excluded": set(), "conflicts": set(), "awaiting": set(ids.values()),
        }
        assert db.count_full_text_candidates(tmp_project.project_id, workflow="assisted") == 0

    def test_the_ai_vote_settles_it_or_raises_a_conflict(self, tmp_project):
        db = tmp_project.db
        agreed, disputed = add_source(tmp_project, "agreed"), add_source(tmp_project, "disputed")
        for sid in (agreed, disputed):
            vote(db, sid, "include", "amber", stage="abstract")
        vote(db, agreed, "include", "gpt", stage="abstract", reviewer_type="ai")
        vote(db, disputed, "exclude", "gpt", stage="abstract", reviewer_type="ai")
        assert _state(tmp_project) == {
            "included": {agreed}, "excluded": set(), "conflicts": {disputed}, "awaiting": set(),
        }

    def test_an_adjudication_settles_it_without_the_ai(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "amber", stage="abstract")
        db.insert_screening_reconciliation(sid, "exclude", "pi", "", stage="abstract")
        state = _state(tmp_project)
        assert (state["excluded"], state["awaiting"]) == ({sid}, set())

    def test_a_flagged_duplicate_is_not_waiting(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "amber", stage="abstract")
        db.mark_source_duplicate(sid, True)
        assert _state(tmp_project)["awaiting"] == set()

    def test_independent_does_not_wait_for_the_ai(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        for rid in ("amber", "bob"):
            vote(db, sid, "include", rid, stage="abstract")
        save_stage_workflow(tmp_project.root, "screening", "independent")
        state = _state(Project(tmp_project.root))
        assert (state["included"], state["awaiting"]) == ({sid}, set())

    def test_prisma_counts_it_as_awaiting_a_decision(self, tmp_project):
        vote(tmp_project.db, add_source(tmp_project), "exclude", "amber", stage="abstract")
        c = prisma_counts(tmp_project)
        assert (c["abstract_screened"], c["abstract_excluded"], c["reports_sought"], c["abstract_pending"]) == (1, 0, 0, 1)


class TestFullText:
    """At full text the AI's verdict comes from AI extraction's flag_check."""

    def _included_by_the_human(self, project):
        sid = add_source(project, with_md=True)
        settle(project.db, sid, "include", stage="abstract")
        vote(project.db, sid, "include", "amber", stage="full_text")
        return sid

    def test_extraction_waits_for_the_ai_full_text_verdict(self, tmp_project):
        sid = self._included_by_the_human(tmp_project)
        state = _state(tmp_project, "full_text")
        assert (state["included"], state["awaiting"]) == (set(), {sid})
        assert _ft_listed("to_extract") == set()

        vote(tmp_project.db, sid, "include", "gpt", stage="full_text", reviewer_type="ai")
        assert _state(tmp_project, "full_text")["included"] == {sid}
        assert _ft_listed("to_extract") == {sid}

    def test_the_card_says_why_there_is_no_extraction_button(self, tmp_project):
        self._included_by_the_human(tmp_project)
        assert "Awaiting the AI's full-text verdict" in component_text(_ft_render("all"))

    def test_the_external_run_template_asks_for_every_paper_that_waits(self, tmp_project):
        """An imported external run gives the AI's verdict, so excluded papers belong in it too."""
        db = tmp_project.db
        included = self._included_by_the_human(tmp_project)
        excluded = add_source(tmp_project, "excluded at full text", with_md=True)
        settle(db, excluded, "include", stage="abstract")
        vote(db, excluded, "exclude", "amber", stage="full_text")
        no_markdown = add_source(tmp_project, "no full text yet")
        settle(db, no_markdown, "include", stage="abstract")
        vote(db, add_source(tmp_project, "abstract, human only", with_md=True), "include", "amber", stage="abstract")

        template = callbacks_of(extract_view)["_download_extract_template"](1)
        assert [r["source_id"] for r in json.loads(template["content"])] == [included, excluded]


class TestFilters:
    def test_the_screening_filter_lists_what_waits_for_the_ai(self, tmp_project):
        db, pid = tmp_project.db, tmp_project.project_id
        waiting = add_source(tmp_project, "human only")
        vote(db, waiting, "include", "amber", stage="abstract")
        settle(db, add_source(tmp_project, "both voted"), "include", stage="abstract")
        vote(db, add_source(tmp_project, "AI only"), "include", "gpt", stage="abstract", reviewer_type="ai")
        add_source(tmp_project, "untouched")
        rows, total, _page = db.list_sources_page(pid, "bob", status="awaiting_ai", team_size=1)
        assert ({s.id for s in rows}, total) == ({waiting}, 1)

    def test_the_full_text_filter_lists_what_waits_for_the_ai(self, tmp_project):
        db = tmp_project.db
        waiting = add_source(tmp_project, "human only at full text", with_md=True)
        both = add_source(tmp_project, "both voted at full text", with_md=True)
        for sid in (waiting, both):
            settle(db, sid, "include", stage="abstract")
        vote(db, waiting, "exclude", "amber", stage="full_text")
        settle(db, both, "exclude", stage="full_text")
        assert _ft_listed("awaiting_ai") == {waiting}


class TestDashboard:
    def test_it_counts_what_waits_for_the_ai_at_each_stage(self, tmp_project):
        db = tmp_project.db
        vote(db, add_source(tmp_project, "abstract, human only"), "include", "amber", stage="abstract")
        ft = add_source(tmp_project, "full text, human only", with_md=True)
        settle(db, ft, "include", stage="abstract")
        vote(db, ft, "exclude", "amber", stage="full_text")
        assert component_text(_build_content("amber")).count("awaiting AI: 1") == 2

    def test_nothing_is_shown_while_nothing_waits(self, tmp_project):
        settle(tmp_project.db, add_source(tmp_project), "include", stage="abstract")
        assert "awaiting AI" not in component_text(_build_content("amber"))

    def test_the_extraction_card_counts_what_the_queue_holds(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project, with_md=True)
        settle(db, sid, "include", stage="abstract")
        vote(db, sid, "include", "amber", stage="full_text")
        db.insert_extraction(ExtractionResult(
            extractor_type="human", extractor_id="amber", field_name="design", value="obs", source_id=sid,
        ))
        db.mark_extraction_submitted(sid, "amber")
        assert "verified by human: 0" in component_text(_build_content("amber"))

        vote(db, sid, "include", "gpt", stage="full_text", reviewer_type="ai")
        assert "verified by human: 1" in component_text(_build_content("amber"))
