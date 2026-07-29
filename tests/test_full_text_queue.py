"""The full-text page's own SQL: which papers reach it, and who still owes an extraction.

TestExtractQueueEligibility (test_extraction_workflow.py) covers the eligibility PREDICATE.
This file covers list_full_text_page, the query the page actually runs — the two came apart
once, which is what these tests exist to stop.
"""

from pathlib import Path

from ailr.core.config import save_stage_workflow
from ailr.core.project import Project
from ailr.core.source import Source
from ailr.exports.prisma import prisma_counts
from ailr.reviewers import ExtractionResult, ScreeningDecision


def _add_source(project, title="Paper", with_md=False):
    sid = project.db.insert_source(Source(title=title, project_id=project.project_id))
    if with_md:
        project.db.update_markdown_path(sid, Path("data/markdown") / f"{sid}.md")
    return sid


def _vote(db, sid, decision, reviewer_id, stage="abstract", reviewer_type="human"):
    db.insert_screening_decision(ScreeningDecision(
        decision=decision, reasoning="test", reviewer_type=reviewer_type,
        reviewer_id=reviewer_id, source_id=sid, stage=stage,
    ))


def _submit(db, sid, extractor_id):
    db.insert_extraction(ExtractionResult(
        extractor_type="human", extractor_id=extractor_id,
        field_name="design", value="observational", source_id=sid,
    ))
    db.mark_extraction_submitted(sid, extractor_id)


def _candidates(db, pid, workflow):
    return set(db.full_text_candidate_ids(pid, workflow=workflow))


def _page(db, pid, reviewer_id, *, status, workflow, team_size, extractors_required=1, exclude_ids=None):
    rows, _total, _page = db.list_full_text_page(
        pid, reviewer_id, status=status, team_size=team_size,
        extractors_required=extractors_required, abstract_workflow=workflow,
        exclude_ids=exclude_ids, page_size=100,
    )
    return {s.id for s in rows}


class TestFullTextCandidates:
    """A paper reaches full text once abstract screening is FINISHED with it and settled on
    include — not on the strength of a single vote from whoever got there first."""

    def test_human_include_is_a_candidate_in_assisted(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _vote(db, sid, "include", "amber")
        assert _candidates(db, tmp_project.project_id, "assisted") == {sid}

    def test_ai_vote_alone_is_not_a_candidate(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _vote(db, sid, "include", "gpt", reviewer_type="ai")
        assert _candidates(db, tmp_project.project_id, "assisted") == set()

    def test_human_exclude_is_not_a_candidate(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _vote(db, sid, "exclude", "amber")
        assert _candidates(db, tmp_project.project_id, "assisted") == set()

    def test_unresolved_assisted_conflict_is_held_back(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _vote(db, sid, "include", "amber")
        _vote(db, sid, "exclude", "gpt", reviewer_type="ai")
        assert _candidates(db, tmp_project.project_id, "assisted") == set()

    def test_adjudicating_the_conflict_releases_it(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _vote(db, sid, "include", "amber")
        _vote(db, sid, "exclude", "gpt", reviewer_type="ai")
        db.insert_screening_reconciliation(sid, "include", adjudicator="pi", stage="abstract")
        assert _candidates(db, tmp_project.project_id, "assisted") == {sid}

    def test_adjudicated_exclude_stays_out(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _vote(db, sid, "include", "amber")
        _vote(db, sid, "exclude", "gpt", reviewer_type="ai")
        db.insert_screening_reconciliation(sid, "exclude", adjudicator="pi", stage="abstract")
        assert _candidates(db, tmp_project.project_id, "assisted") == set()

    def test_independent_needs_both_reviewers(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _vote(db, sid, "include", "amber")
        assert _candidates(db, tmp_project.project_id, "independent") == set()
        _vote(db, sid, "include", "bob")
        assert _candidates(db, tmp_project.project_id, "independent") == {sid}

    def test_independent_disagreement_is_held_back(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _vote(db, sid, "include", "amber")
        _vote(db, sid, "exclude", "bob")
        assert _candidates(db, tmp_project.project_id, "independent") == set()

    def test_the_page_and_the_candidate_count_agree(self, tmp_project):
        db = tmp_project.db
        pid = tmp_project.project_id
        settled = _add_source(tmp_project)
        _vote(db, settled, "include", "amber")
        pending = _add_source(tmp_project)
        _vote(db, pending, "include", "gpt", reviewer_type="ai")  # AI only: not finished

        assert db.count_full_text_candidates(pid, workflow="assisted") == 1
        assert _page(db, pid, "amber", status="all", workflow="assisted", team_size=1) == {settled}


class TestToExtractQueue:
    """Under independent extraction two humans each extract the paper, so the queue has to stay
    open to the second one after the first submits."""

    def _eligible_paper(self, project):
        """A paper settled as an include at both stages, with markdown — extraction-ready."""
        sid = _add_source(project, with_md=True)
        _vote(project.db, sid, "include", "amber")
        _vote(project.db, sid, "include", "amber", stage="full_text")
        return sid

    def test_unstarted_paper_is_queued_for_everyone(self, tmp_project):
        db, pid = tmp_project.db, tmp_project.project_id
        sid = self._eligible_paper(tmp_project)
        for rid in ("amber", "bob"):
            assert _page(db, pid, rid, status="to_extract", workflow="assisted",
                         team_size=1, extractors_required=2) == {sid}

    def test_independent_keeps_it_queued_for_the_second_extractor(self, tmp_project):
        db, pid = tmp_project.db, tmp_project.project_id
        sid = self._eligible_paper(tmp_project)
        _submit(db, sid, "amber")

        assert _page(db, pid, "amber", status="to_extract", workflow="assisted",
                     team_size=1, extractors_required=2) == set()
        assert _page(db, pid, "bob", status="to_extract", workflow="assisted",
                     team_size=1, extractors_required=2) == {sid}

    def test_independent_clears_once_both_have_submitted(self, tmp_project):
        db, pid = tmp_project.db, tmp_project.project_id
        sid = self._eligible_paper(tmp_project)
        _submit(db, sid, "amber")
        _submit(db, sid, "bob")

        for rid in ("amber", "bob"):
            assert _page(db, pid, rid, status="to_extract", workflow="assisted",
                         team_size=1, extractors_required=2) == set()
        assert db.sources_needing_consensus([sid]) == {sid}

    def test_verify_clears_for_everyone_after_one_submit(self, tmp_project):
        db, pid = tmp_project.db, tmp_project.project_id
        sid = self._eligible_paper(tmp_project)
        assert _page(db, pid, "amber", status="to_extract", workflow="assisted",
                     team_size=1, extractors_required=1) == {sid}
        _submit(db, sid, "amber")

        for rid in ("amber", "bob"):
            assert _page(db, pid, rid, status="to_extract", workflow="assisted",
                         team_size=1, extractors_required=1) == set()

    def test_paper_without_markdown_is_never_queued(self, tmp_project):
        db, pid = tmp_project.db, tmp_project.project_id
        sid = _add_source(tmp_project, with_md=False)
        _vote(db, sid, "include", "amber")
        _vote(db, sid, "include", "amber", stage="full_text")
        queued = self._eligible_paper(tmp_project)   # same route, but with markdown
        assert _page(db, pid, "amber", status="to_extract", workflow="assisted",
                     team_size=1, extractors_required=2) == {queued}


class TestQueueMatchesPrisma:
    """The page and the reported flow must count the same papers. This is the invariant that
    catches the two definitions drifting apart again."""

    def _counts_agree(self, project):
        workflow = project.config.screening_workflow("abstract")
        page = project.db.count_full_text_candidates(project.project_id, workflow=workflow)
        return page, prisma_counts(project)["reports_sought"]

    def test_assisted_page_count_equals_reports_sought(self, tmp_project):
        db = tmp_project.db
        _vote(db, _add_source(tmp_project), "include", "amber")
        _vote(db, _add_source(tmp_project), "exclude", "amber")

        held = _add_source(tmp_project)  # unresolved AI-vs-human conflict
        _vote(db, held, "include", "amber")
        _vote(db, held, "exclude", "gpt", reviewer_type="ai")

        ai_only = _add_source(tmp_project)  # no human has screened it
        _vote(db, ai_only, "include", "gpt", reviewer_type="ai")

        page, sought = self._counts_agree(tmp_project)
        assert page == sought == 1

    def test_independent_page_count_equals_reports_sought(self, tmp_project):
        save_stage_workflow(tmp_project.root, "screening", "independent")
        project = Project(tmp_project.root)
        db = project.db

        both = _add_source(project)
        _vote(db, both, "include", "amber")
        _vote(db, both, "include", "bob")

        half = _add_source(project)  # only one of the two reviewers has voted
        _vote(db, half, "include", "amber")

        split = _add_source(project)  # disagreement, not yet adjudicated
        _vote(db, split, "include", "amber")
        _vote(db, split, "exclude", "bob")

        page, sought = self._counts_agree(project)
        assert page == sought == 1

    def test_empty_project_agrees_at_zero(self, tmp_project):
        page, sought = self._counts_agree(tmp_project)
        assert page == sought == 0
