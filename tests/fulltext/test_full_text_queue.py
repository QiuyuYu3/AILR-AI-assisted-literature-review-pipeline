"""The full-text page's own SQL: which papers reach it, and who still owes an extraction.

TestExtractQueueEligibility (test_extraction_workflow.py) covers the eligibility PREDICATE.
This file covers list_full_text_page, the query the page actually runs — the two came apart
once, which is what these tests exist to stop.
"""

from ailr.core.config import save_stage_workflow
from ailr.core.project import Project
from ailr.exports.prisma import prisma_counts
from ailr.reviewers import ExtractionResult
from ailr.ui import full_text_view
from tests.helpers import add_source, callbacks_of, settle, vote, walk


def _draft(db, sid, extractor_id, field_name="design"):
    """Save without submitting — what the extraction form's Save button writes."""
    db.insert_extraction(ExtractionResult(
        extractor_type="human", extractor_id=extractor_id,
        field_name=field_name, value="observational", source_id=sid,
    ))


def _submit(db, sid, extractor_id):
    _draft(db, sid, extractor_id)
    db.mark_extraction_submitted(sid, extractor_id)


def _extraction_ready(project):
    """A paper settled as an include at both stages, with markdown — extraction-ready."""
    sid = add_source(project, with_md=True)
    settle(project.db, sid, "include", stage="abstract")
    settle(project.db, sid, "include", stage="full_text")
    return sid


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
        sid = add_source(tmp_project)
        settle(db, sid, "include", stage="abstract")
        assert _candidates(db, tmp_project.project_id, "assisted") == {sid}

    def test_ai_vote_alone_is_not_a_candidate(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "gpt", stage="abstract", reviewer_type="ai")
        assert _candidates(db, tmp_project.project_id, "assisted") == set()

    def test_human_exclude_is_not_a_candidate(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        settle(db, sid, "exclude", stage="abstract")
        assert _candidates(db, tmp_project.project_id, "assisted") == set()

    def test_unresolved_assisted_conflict_is_held_back(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "amber", stage="abstract")
        vote(db, sid, "exclude", "gpt", stage="abstract", reviewer_type="ai")
        assert _candidates(db, tmp_project.project_id, "assisted") == set()

    def test_adjudicating_the_conflict_releases_it(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "amber", stage="abstract")
        vote(db, sid, "exclude", "gpt", stage="abstract", reviewer_type="ai")
        db.insert_screening_reconciliation(sid, "include", adjudicator="pi", stage="abstract")
        assert _candidates(db, tmp_project.project_id, "assisted") == {sid}

    def test_adjudicated_exclude_stays_out(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "amber", stage="abstract")
        vote(db, sid, "exclude", "gpt", stage="abstract", reviewer_type="ai")
        db.insert_screening_reconciliation(sid, "exclude", adjudicator="pi", stage="abstract")
        assert _candidates(db, tmp_project.project_id, "assisted") == set()

    def test_independent_needs_both_reviewers(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "amber", stage="abstract")
        assert _candidates(db, tmp_project.project_id, "independent") == set()
        vote(db, sid, "include", "bob", stage="abstract")
        assert _candidates(db, tmp_project.project_id, "independent") == {sid}

    def test_independent_disagreement_is_held_back(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "amber", stage="abstract")
        vote(db, sid, "exclude", "bob", stage="abstract")
        assert _candidates(db, tmp_project.project_id, "independent") == set()

    def test_the_page_and_the_candidate_count_agree(self, tmp_project):
        db = tmp_project.db
        pid = tmp_project.project_id
        settled = add_source(tmp_project)
        settle(db, settled, "include", stage="abstract")
        pending = add_source(tmp_project)
        vote(db, pending, "include", "gpt", stage="abstract", reviewer_type="ai")  # AI only: not finished

        assert db.count_full_text_candidates(pid, workflow="assisted") == 1
        assert _page(db, pid, "amber", status="all", workflow="assisted", team_size=1) == {settled}


class TestReviewQueue:
    """The page every reviewer lands on: papers they have not judged at full text while the stage
    still has room for another human, and next to it the ones they have judged."""

    def test_in_assisted_one_vote_takes_it_off_everyones_queue(self, tmp_project):
        db, pid = tmp_project.db, tmp_project.project_id
        sid = add_source(tmp_project)
        settle(db, sid, "include", stage="abstract")  # amber's abstract vote is not a full-text one
        for rid in ("amber", "bob"):
            assert _page(db, pid, rid, status="to_review", workflow="assisted", team_size=1) == {sid}
            assert _page(db, pid, rid, status="reviewed", workflow="assisted", team_size=1) == set()

        vote(db, sid, "include", "amber", stage="full_text")
        for rid in ("amber", "bob"):
            assert _page(db, pid, rid, status="to_review", workflow="assisted", team_size=1) == set()
        assert _page(db, pid, "amber", status="reviewed", workflow="assisted", team_size=1) == {sid}
        assert _page(db, pid, "bob", status="reviewed", workflow="assisted", team_size=1) == set()

    def test_in_independent_it_stays_open_for_the_second_reviewer_only(self, tmp_project):
        db, pid = tmp_project.db, tmp_project.project_id
        sid = add_source(tmp_project)
        settle(db, sid, "include", stage="abstract")
        vote(db, sid, "include", "amber", stage="full_text")
        assert _page(db, pid, "amber", status="to_review", workflow="assisted", team_size=2) == set()
        assert _page(db, pid, "bob", status="to_review", workflow="assisted", team_size=2) == {sid}

        vote(db, sid, "exclude", "bob", stage="full_text")
        assert _page(db, pid, "carol", status="to_review", workflow="assisted", team_size=2) == set()


class TestToExtractQueue:
    """Under independent extraction two humans each extract the paper, so the queue has to stay
    open to the second one after the first submits."""

    def _eligible_paper(self, project):
        return _extraction_ready(project)

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

    def test_under_independent_full_text_both_reviewers_must_include_first(self, tmp_project):
        db, pid = tmp_project.db, tmp_project.project_id
        sid = add_source(tmp_project, with_md=True)
        settle(db, sid, "include", stage="abstract")
        vote(db, sid, "include", "amber", stage="full_text")
        assert _page(db, pid, "amber", status="to_extract", workflow="assisted", team_size=2) == set()

        vote(db, sid, "include", "bob", stage="full_text")
        assert _page(db, pid, "amber", status="to_extract", workflow="assisted", team_size=2) == {sid}

    def test_in_verify_a_saved_draft_claims_the_paper(self, tmp_project):
        """One extractor per paper: amber's draft takes it out of everyone else's queue."""
        db, pid = tmp_project.db, tmp_project.project_id
        sid = _extraction_ready(tmp_project)
        _draft(db, sid, "amber")
        assert _page(db, pid, "bob", status="to_extract", workflow="assisted", team_size=1) == set()
        assert _page(db, pid, "amber", status="to_extract", workflow="assisted", team_size=1) == {sid}

    def test_in_independent_a_third_extractor_is_not_offered_a_finished_paper(self, tmp_project):
        db, pid = tmp_project.db, tmp_project.project_id
        sid = _extraction_ready(tmp_project)
        _submit(db, sid, "amber")
        _submit(db, sid, "bob")
        assert _page(db, pid, "carol", status="to_extract", workflow="assisted", team_size=1,
                     extractors_required=2) == set()

    def test_paper_without_markdown_is_never_queued(self, tmp_project):
        db, pid = tmp_project.db, tmp_project.project_id
        sid = add_source(tmp_project, with_md=False)
        settle(db, sid, "include", stage="abstract")
        settle(db, sid, "include", stage="full_text")
        queued = self._eligible_paper(tmp_project)   # same route, but with markdown
        assert _page(db, pid, "amber", status="to_extract", workflow="assisted",
                     team_size=1, extractors_required=2) == {queued}


class TestMyDraftFilter:
    """`my_draft` = saved by me, not submitted yet. It is deliberately a SUBSET of `to_extract`:
    saving must not make a paper vanish from the queue it is still owed on."""

    def test_a_saved_draft_shows_up(self, tmp_project):
        db, pid = tmp_project.db, tmp_project.project_id
        sid = _extraction_ready(tmp_project)
        _draft(db, sid, "amber")
        assert _page(db, pid, "amber", status="my_draft", workflow="assisted", team_size=1) == {sid}

    def test_an_untouched_paper_does_not(self, tmp_project):
        db, pid = tmp_project.db, tmp_project.project_id
        _extraction_ready(tmp_project)
        assert _page(db, pid, "amber", status="my_draft", workflow="assisted", team_size=1) == set()

    def test_a_draft_is_still_in_the_to_extract_queue(self, tmp_project):
        db, pid = tmp_project.db, tmp_project.project_id
        sid = _extraction_ready(tmp_project)
        _draft(db, sid, "amber")
        assert _page(db, pid, "amber", status="to_extract", workflow="assisted", team_size=1) == {sid}

    def test_submitting_clears_it(self, tmp_project):
        db, pid = tmp_project.db, tmp_project.project_id
        sid = _extraction_ready(tmp_project)
        _submit(db, sid, "amber")
        assert _page(db, pid, "amber", status="my_draft", workflow="assisted", team_size=1) == set()
        assert _page(db, pid, "amber", status="extracted_mine", workflow="assisted", team_size=1) == {sid}

    def test_extracted_by_me_keeps_the_first_of_two_submitters(self, tmp_project):
        """Independent extraction: a second reviewer's submission must not take the paper out of
        the first reviewer's own "Extracted by me" list."""
        db, pid = tmp_project.db, tmp_project.project_id
        sid = _extraction_ready(tmp_project)
        _submit(db, sid, "amber")
        _submit(db, sid, "bob")
        for rid in ("amber", "bob"):
            assert _page(db, pid, rid, status="extracted_mine", workflow="assisted", team_size=1,
                         extractors_required=2) == {sid}

    def test_someone_elses_draft_is_not_mine(self, tmp_project):
        db, pid = tmp_project.db, tmp_project.project_id
        sid = _extraction_ready(tmp_project)
        _draft(db, sid, "bob")
        assert _page(db, pid, "amber", status="my_draft", workflow="assisted", team_size=1) == set()
        assert _page(db, pid, "bob", status="my_draft", workflow="assisted", team_size=1) == {sid}

    def test_a_flag_check_row_alone_is_not_a_draft(self, tmp_project):
        # '_flag_check' is a screening verdict written into the extractions table, not extracted data.
        db, pid = tmp_project.db, tmp_project.project_id
        sid = _extraction_ready(tmp_project)
        _draft(db, sid, "amber", field_name="_flag_check")
        assert _page(db, pid, "amber", status="my_draft", workflow="assisted", team_size=1) == set()

    def test_an_unresolved_full_text_conflict_is_held_back(self, tmp_project):
        db, pid = tmp_project.db, tmp_project.project_id
        sid = _extraction_ready(tmp_project)
        _draft(db, sid, "amber")
        assert _page(db, pid, "amber", status="my_draft", workflow="assisted", team_size=1,
                     exclude_ids={sid}) == set()


class TestQueuePage:
    """The page callback works out the papers still in conflict itself; the SQL only filters them."""

    def _listed(self, status):
        render = callbacks_of(full_text_view)["_render"]
        cards, *_ = render(status, None, "amber", None, None, "", "title_and_abstract", None, None, "id", 50, None, None)
        return {n.id["source"] for n in walk(cards) if isinstance(getattr(n, "id", None), dict) and "source" in n.id}

    def test_a_paper_in_full_text_conflict_waits_for_its_ruling_before_extraction(self, tmp_project):
        db = tmp_project.db
        clear = _extraction_ready(tmp_project)
        conflicted = _extraction_ready(tmp_project)
        vote(db, conflicted, "exclude", "gpt", stage="full_text", reviewer_type="ai")
        assert self._listed("to_extract") == {clear}

        db.insert_screening_reconciliation(conflicted, "include", "pi", "", stage="full_text")
        assert self._listed("to_extract") == {clear, conflicted}


class TestQueueMatchesPrisma:
    """The page and the reported flow must count the same papers. This is the invariant that
    catches the two definitions drifting apart again."""

    def _counts_agree(self, project):
        workflow = project.config.screening_workflow("abstract")
        page = project.db.count_full_text_candidates(project.project_id, workflow=workflow)
        return page, prisma_counts(project)["reports_sought"]

    def test_assisted_page_count_equals_reports_sought(self, tmp_project):
        db = tmp_project.db
        settle(db, add_source(tmp_project), "include", stage="abstract")
        settle(db, add_source(tmp_project), "exclude", stage="abstract")

        held = add_source(tmp_project)  # unresolved AI-vs-human conflict
        vote(db, held, "include", "amber", stage="abstract")
        vote(db, held, "exclude", "gpt", stage="abstract", reviewer_type="ai")

        ai_only = add_source(tmp_project)  # no human has screened it
        vote(db, ai_only, "include", "gpt", stage="abstract", reviewer_type="ai")

        page, sought = self._counts_agree(tmp_project)
        assert page == sought == 1

    def test_independent_page_count_equals_reports_sought(self, tmp_project):
        save_stage_workflow(tmp_project.root, "screening", "independent")
        project = Project(tmp_project.root)
        db = project.db

        both = add_source(project)
        vote(db, both, "include", "amber", stage="abstract")
        vote(db, both, "include", "bob", stage="abstract")

        half = add_source(project)  # only one of the two reviewers has voted
        vote(db, half, "include", "amber", stage="abstract")

        split = add_source(project)  # disagreement, not yet adjudicated
        vote(db, split, "include", "amber", stage="abstract")
        vote(db, split, "exclude", "bob", stage="abstract")

        page, sought = self._counts_agree(project)
        assert page == sought == 1

    def test_a_flagged_duplicate_leaves_both_counts(self, tmp_project):
        db = tmp_project.db
        keep, copy = add_source(tmp_project, "Paper"), add_source(tmp_project, "Paper, again")
        for sid in (keep, copy):
            settle(db, sid, "include", stage="abstract")
        db.mark_source_duplicate(copy, True)
        page, sought = self._counts_agree(tmp_project)
        assert page == sought == 1

    def test_empty_project_agrees_at_zero(self, tmp_project):
        page, sought = self._counts_agree(tmp_project)
        assert page == sought == 0
