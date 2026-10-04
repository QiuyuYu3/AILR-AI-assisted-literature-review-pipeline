"""PRISMA / report numbers — the counts that end up in the manuscript.

Regressions guarded:
- 0.17: stage counts use latest-only decisions (a re-vote must not inflate 'records screened')
- 0.17: the Markdown report and the SVG diagram share ONE set of counts (they used to disagree)
- methods skeleton reports κ over the same latest-only pairing as Reports
"""

import json
import re
from pathlib import Path

import pytest

from ailr.core.config import save_project_type, save_registration, save_stage_workflow
from ailr.core.project import Project
from ailr.core.source import Source
from ailr.exports.methods import build_methods_skeleton
from ailr.exports.prisma import build_prisma_report, build_prisma_svg, prisma_counts
from ailr.llm.base import CallMetadata
from ailr.metrics import (
    BINARY_CATEGORIES,
    binarize,
    cohen_kappa_ci,
    decisions_for_pair,
    rater_overlaps,
)
from ailr.reviewers import ExtractionResult, ScreeningDecision
from ailr.ui import reports_view
from tests.helpers import add_source, component_text, set_config, vote


def _add_source(project, title, with_md=False):
    return add_source(project, title, with_md=with_md, source_database="test-db")


def _pipeline_state(project):
    """4 sources + 1 dropped duplicate; s1 fully through the pipeline, s2 excluded after a
    re-vote, s3 included but PDF never retrieved, s4 untouched."""
    db = project.db
    s1 = _add_source(project, "S1 full pipeline", with_md=True)
    s2 = _add_source(project, "S2 excluded on revote")
    s3 = _add_source(project, "S3 include without pdf")
    _add_source(project, "S4 unscreened")
    db.insert_duplicate(project.project_id, "dropped dup", None, "doi")

    vote(db, s1, "include", "amber", stage="abstract")
    vote(db, s2, "include", "amber", stage="abstract")
    vote(db, s2, "exclude", "amber", stage="abstract")  # re-vote: only the latest may count
    vote(db, s3, "include", "amber", stage="abstract")
    vote(db, s1, "include", "gpt", stage="abstract", reviewer_type="ai")
    vote(db, s2, "exclude", "gpt", stage="abstract", reviewer_type="ai")

    vote(db, s1, "include", "amber", stage="full_text")
    db.insert_extraction(ExtractionResult(
        extractor_type="ai", extractor_id="gpt", field_name="design", value="obs", source_id=s1,
    ))
    db.insert_extraction(ExtractionResult(
        extractor_type="human", extractor_id="amber", field_name="design", value="obs", source_id=s1,
    ))
    db.mark_extraction_submitted(s1, "amber")
    return s1, s2, s3


class TestPrismaCounts:
    def test_flow_counts(self, tmp_project):
        _pipeline_state(tmp_project)
        c = prisma_counts(tmp_project)
        assert c["records_identified"] == 5          # 4 kept + 1 duplicate
        assert c["duplicates_removed"] == 1
        assert c["records_after_dedup"] == 4
        assert c["abstract_screened"] == 3           # s1 s2 s3; s2's re-vote counted ONCE
        assert c["abstract_excluded"] == 1
        assert c["reports_sought"] == 2              # latest-include: s1, s3 (not s2)
        # s3 has no markdown, but nobody has said the full text is unobtainable, so it is work
        # outstanding rather than a retrieval failure.
        assert c["reports_retrieved"] == 2
        assert c["reports_not_retrieved"] == 0
        assert c["full_text_assessed"] == 1
        assert c["studies_included"] == c["reports_included"] == 1   # s3 is an include at title/abstract only
        assert c["studies_extracted"] == 1
        assert c["ai_abstract_screened"] == 2

    def test_not_retrieved_is_marked_not_inferred(self, tmp_project):
        """PRISMA's 'not retrieved' means sought and unobtainable. A missing markdown alone is
        just work outstanding, so only an explicit mark moves a report into that box."""
        _s1, s2, s3 = _pipeline_state(tmp_project)
        db = tmp_project.db

        db.set_full_text_not_retrieved(s3, True)
        c = prisma_counts(tmp_project)
        assert c["reports_sought"] == 2
        assert c["reports_retrieved"] == 1
        assert c["reports_not_retrieved"] == 1

        db.set_full_text_not_retrieved(s3, False)
        assert prisma_counts(tmp_project)["reports_not_retrieved"] == 0
        assert db.get_source(s3).full_text_not_retrieved is False

        # s2 was excluded at abstract, so it was never sought and cannot be a retrieval failure.
        db.set_full_text_not_retrieved(s2, True)
        assert prisma_counts(tmp_project)["reports_not_retrieved"] == 0

    def test_flow_numbers_count_papers_not_decisions(self, tmp_project):
        """Independent mode: two reviewers both including one paper must count it ONCE."""
        db = tmp_project.db
        sid = _add_source(tmp_project, "doubly included", with_md=True)
        vote(db, sid, "include", "amber", stage="abstract")
        vote(db, sid, "include", "bob", stage="abstract")
        vote(db, sid, "include", "amber", stage="full_text")
        vote(db, sid, "include", "bob", stage="full_text")
        c = prisma_counts(tmp_project)
        assert c["abstract_screened"] == 1
        assert c["reports_sought"] == 1
        assert c["reports_retrieved"] == 1
        assert c["reports_not_retrieved"] == 0
        assert c["full_text_assessed"] == 1
        assert c["studies_included"] == 1

    def test_reconciliation_overrides_votes_in_the_flow(self, tmp_project):
        db = tmp_project.db
        s_out = _add_source(tmp_project, "included then adjudicated out", with_md=True)
        vote(db, s_out, "include", "amber", stage="full_text")
        db.insert_screening_reconciliation(s_out, "exclude", adjudicator="pi", stage="full_text")
        s_in = _add_source(tmp_project, "excluded then adjudicated in", with_md=True)
        vote(db, s_in, "exclude", "amber", stage="full_text")
        db.insert_screening_reconciliation(s_in, "include", adjudicator="pi", stage="full_text")
        c = prisma_counts(tmp_project)
        assert c["studies_included"] == 1  # only the adjudicated-in paper

    def test_empty_project_is_all_zeros(self, tmp_project):
        c = prisma_counts(tmp_project)
        assert c["records_identified"] == 0
        assert c["abstract_screened"] == 0
        assert c["studies_included"] == 0


class TestPrismaFollowsTheStageWorkflows:
    """A paper is included at a stage only once that stage is settled for it, and each screening
    stage decides what settled means through its own workflow.
    """

    def _reload(self, tmp_project):
        return Project(tmp_project.root)

    def test_independent_full_text_waits_for_the_second_reviewer(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, "one full-text vote", with_md=True)
        vote(db, sid, "include", "amber", stage="abstract")
        vote(db, sid, "include", "bob", stage="abstract")
        vote(db, sid, "include", "amber", stage="full_text")
        save_stage_workflow(tmp_project.root, "screening", "independent")

        project = self._reload(tmp_project)
        assert prisma_counts(project)["reports_sought"] == 1
        assert prisma_counts(project)["studies_included"] == 0

        vote(project.db, sid, "include", "bob", stage="full_text")
        assert prisma_counts(project)["studies_included"] == 1

    def test_the_full_text_override_gates_the_included_box(self, tmp_project):
        """Abstract independent, full text assisted: two abstract votes, but one full-text vote
        settles the stage."""
        db = tmp_project.db
        sid = _add_source(tmp_project, "assisted at full text", with_md=True)
        vote(db, sid, "include", "amber", stage="abstract")
        vote(db, sid, "include", "bob", stage="abstract")
        vote(db, sid, "include", "amber", stage="full_text")
        save_stage_workflow(tmp_project.root, "screening", "independent")
        save_stage_workflow(tmp_project.root, "full_text_screening", "assisted")

        assert prisma_counts(self._reload(tmp_project))["studies_included"] == 1

    def test_a_half_screened_paper_is_not_yet_sought(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, "one abstract vote", with_md=True)
        vote(db, sid, "include", "amber", stage="abstract")
        save_stage_workflow(tmp_project.root, "screening", "independent")

        c = prisma_counts(self._reload(tmp_project))
        assert c["abstract_screened"] == 1   # it has been looked at
        assert c["reports_sought"] == 0      # but the stage is not finished with it

    def test_an_unresolved_disagreement_counts_as_neither(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, "unresolved", with_md=True)
        vote(db, sid, "include", "amber", stage="abstract")
        vote(db, sid, "exclude", "bob", stage="abstract")
        save_stage_workflow(tmp_project.root, "screening", "independent")

        c = prisma_counts(self._reload(tmp_project))
        assert c["reports_sought"] == 0
        assert c["studies_included"] == 0


class TestExcludedBoxesFollowTheSettledRule:
    """An excluded box counts papers whose stage is settled on exclude, mirroring the included
    rule, so each screened paper is excluded, sought, or still awaiting a decision, exactly once."""

    def _reload(self, tmp_project):
        return Project(tmp_project.root)

    def test_two_reviewers_excluding_one_record_count_it_once(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, "excluded by both")
        vote(db, sid, "exclude", "amber", stage="abstract")
        vote(db, sid, "exclude", "bob", stage="abstract")
        save_stage_workflow(tmp_project.root, "screening", "independent")

        c = prisma_counts(self._reload(tmp_project))
        assert (c["abstract_screened"], c["abstract_excluded"], c["abstract_pending"]) == (1, 1, 0)

    def test_a_record_in_conflict_is_pending_not_excluded(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, "human exclude, AI include")
        vote(db, sid, "include", "gpt", stage="abstract", reviewer_type="ai")
        vote(db, sid, "exclude", "amber", stage="abstract")

        c = prisma_counts(tmp_project)
        assert (c["abstract_excluded"], c["reports_sought"], c["abstract_pending"]) == (0, 0, 1)

    def test_an_adjudicated_record_is_counted_on_one_side_only(self, tmp_project):
        db = tmp_project.db
        for title, verdict in (("adjudicated in", "include"), ("adjudicated out", "exclude")):
            sid = _add_source(tmp_project, title)
            vote(db, sid, "include", "amber", stage="abstract")
            vote(db, sid, "exclude", "bob", stage="abstract")
            db.insert_screening_reconciliation(sid, verdict, "pi", "discussed", stage="abstract")
        save_stage_workflow(tmp_project.root, "screening", "independent")

        c = prisma_counts(self._reload(tmp_project))
        assert (c["abstract_excluded"], c["reports_sought"], c["abstract_pending"]) == (1, 1, 0)

    def test_the_title_abstract_flow_balances(self, tmp_project):
        db = tmp_project.db
        votes = {"agreed in": ("include", "include"), "agreed out": ("exclude", "exclude"),
                 "split": ("include", "exclude"), "adjudicated out": ("include", "exclude")}
        ids = {}
        for title, (amber, bob) in votes.items():
            ids[title] = _add_source(tmp_project, title)
            vote(db, ids[title], amber, "amber", stage="abstract")
            vote(db, ids[title], bob, "bob", stage="abstract")
        vote(db, _add_source(tmp_project, "one vote so far"), "exclude", "amber", stage="abstract")
        db.insert_screening_reconciliation(ids["adjudicated out"], "exclude", "pi", "", stage="abstract")
        save_stage_workflow(tmp_project.root, "screening", "independent")

        c = prisma_counts(self._reload(tmp_project))
        assert (c["abstract_excluded"], c["reports_sought"], c["abstract_pending"]) == (2, 1, 2)
        assert c["abstract_screened"] == c["abstract_excluded"] + c["reports_sought"] + c["abstract_pending"]

    def test_an_unresolved_full_text_conflict_is_not_an_excluded_report(self, tmp_project):
        db = tmp_project.db
        db.create_exclusion_reason(tmp_project.project_id, "Wrong population")
        sid = _add_source(tmp_project, "full-text split", with_md=True)
        vote(db, sid, "include", "amber", stage="abstract")
        vote(db, sid, "include", "bob", stage="abstract")
        vote(db, sid, "exclude", "amber", stage="full_text", reasoning="Wrong population")
        vote(db, sid, "include", "bob", stage="full_text")
        save_stage_workflow(tmp_project.root, "screening", "independent")

        c = prisma_counts(self._reload(tmp_project))
        assert (c["full_text_excluded_reports"], c["studies_included"], c["full_text_pending"]) == (0, 0, 1)
        assert c["full_text_exclusion_reasons"] == []

    def test_full_text_reasons_come_from_each_reviewers_latest_vote(self, tmp_project):
        db = tmp_project.db
        for name in ("Wrong population", "Wrong outcome"):
            db.create_exclusion_reason(tmp_project.project_id, name)
        sid = _add_source(tmp_project, "re-voted exclusion", with_md=True)
        vote(db, sid, "include", "amber", stage="abstract")
        vote(db, sid, "exclude", "amber", stage="full_text", reasoning="Wrong population")
        vote(db, sid, "exclude", "amber", stage="full_text", reasoning="Wrong outcome")

        c = prisma_counts(tmp_project)
        assert c["full_text_exclusion_reasons"] == [{"reason": "Wrong outcome", "n": 1}]


class TestFlaggedDuplicates:
    """A duplicate found by hand during screening was, in PRISMA terms, removed before screening."""

    def test_a_flagged_duplicate_moves_to_duplicates_removed(self, tmp_project):
        db = tmp_project.db
        keep = _add_source(tmp_project, "Paper C")
        copy = _add_source(tmp_project, "Paper C, second copy")
        for sid in (keep, copy):
            vote(db, sid, "include", "amber", stage="abstract")
        db.mark_source_duplicate(copy, True)

        c = prisma_counts(tmp_project)
        assert c["records_identified"] == 2
        assert (c["duplicates_removed"], c["records_after_dedup"]) == (1, 1)
        assert (c["abstract_screened"], c["reports_sought"]) == (1, 1)

    def test_a_flagged_duplicate_leaves_the_included_box(self, tmp_project):
        db = tmp_project.db
        keep = _add_source(tmp_project, "Paper D", with_md=True)
        copy = _add_source(tmp_project, "Paper D, second copy", with_md=True)
        for sid in (keep, copy):
            vote(db, sid, "include", "amber", stage="abstract")
            vote(db, sid, "include", "amber", stage="full_text")
        db.mark_source_duplicate(copy, True)

        c = prisma_counts(tmp_project)
        assert (c["full_text_assessed"], c["studies_included"], c["reports_included"]) == (1, 1, 1)


class TestMethodsSkeleton:
    """A placeholder: the methods text is prose that changes often, so this only checks it
    describes the dedup rule actually in force and names the full-text design."""

    def test_describes_the_title_rule_and_the_guards_in_force(self, tmp_project):
        _add_source(tmp_project, "S1")
        text = build_methods_skeleton(tmp_project)
        assert ("by exact DOI match, then by identical normalized titles unless DOI, publication year or "
                "first author conflicted") in text
        assert "fuzzy" not in text and "threshold" not in text

    def test_describes_the_full_text_design(self, tmp_project):
        _add_source(tmp_project, "S1")
        save_stage_workflow(tmp_project.root, "full_text_screening", "independent")

        text = build_methods_skeleton(Project(tmp_project.root))
        assert "Full texts" in text
        assert "independently by two human" in text


class TestMethodsNumbers:
    """Numbers and claims in the methods text, each checked against data where a wrong source for
    the number would give a different answer."""

    def _included_at_full_text(self, project, title):
        sid = _add_source(project, title, with_md=True)
        vote(project.db, sid, "include", "amber", stage="abstract")
        vote(project.db, sid, "include", "amber", stage="full_text")
        return sid

    def test_independent_screening_reports_records_not_decisions(self, tmp_project):
        db = tmp_project.db
        for title in ("P1", "P2"):
            sid = _add_source(tmp_project, title)
            vote(db, sid, "include", "amber", stage="abstract")
            vote(db, sid, "include", "bob", stage="abstract")
        save_stage_workflow(tmp_project.root, "screening", "independent")

        text = build_methods_skeleton(Project(tmp_project.root))
        assert "2 records were screened by human reviewers" in text
        assert "screening decisions" not in text

    def test_extraction_counts_only_papers_a_human_has_finished(self, tmp_project):
        """verify: a paper is done once its checker has submitted; an AI pass alone is not."""
        db = tmp_project.db
        checked = self._included_at_full_text(tmp_project, "checked")
        ai_only = self._included_at_full_text(tmp_project, "AI pass only")
        for sid in (checked, ai_only):
            db.insert_extraction(ExtractionResult(
                extractor_type="ai", extractor_id="gpt", field_name="design", value="obs", source_id=sid))
        db.insert_extraction(ExtractionResult(
            extractor_type="human", extractor_id="amber", field_name="design", value="obs", source_id=checked))
        db.mark_extraction_submitted(checked, "amber")

        assert prisma_counts(tmp_project)["studies_extracted"] == 1
        assert "Extraction was completed for 1 of the 2 included studies." in build_methods_skeleton(tmp_project)

    def test_independent_extraction_is_done_once_the_consensus_is_saved(self, tmp_project):
        db = tmp_project.db
        reconciled = self._included_at_full_text(tmp_project, "reconciled")
        waiting = self._included_at_full_text(tmp_project, "waiting for consensus")
        for sid in (reconciled, waiting):
            for rid in ("amber", "bob"):
                db.insert_extraction(ExtractionResult(
                    extractor_type="human", extractor_id=rid, field_name="design", value="obs", source_id=sid))
                db.mark_extraction_submitted(sid, rid)
        db.save_consensus(reconciled, "pi", [ExtractionResult(
            extractor_type="consensus", extractor_id="pi", field_name="design", value="obs")])
        save_stage_workflow(tmp_project.root, "extraction", "independent")

        project = Project(tmp_project.root)
        assert prisma_counts(project)["studies_extracted"] == 1
        extraction = build_methods_skeleton(project).split("## Full-text extraction")[1]
        assert "Extraction was completed for 1 of the 2 included studies." in extraction
        assert "two human reviewers" in extraction and "consensus" in extraction

    def test_uncertain_votes_are_explained_without_claiming_they_advance(self, tmp_project):
        _pipeline_state(tmp_project)
        text = build_methods_skeleton(tmp_project)
        assert "an uncertain vote does not exclude a record" in text
        assert "carries forward to the next stage" not in text

    def test_hand_flagged_duplicates_are_mentioned_only_when_there_are_some(self, tmp_project):
        flagged = [_add_source(tmp_project, title) for title in ("A", "A again", "A once more")][1:]
        assert "flagged by hand" not in build_methods_skeleton(tmp_project)

        for sid in flagged:
            tmp_project.db.mark_source_duplicate(sid, True)
        assert "A further 2 records were flagged by hand as duplicates" in build_methods_skeleton(tmp_project)


# 12 AI/human pairs that read differently binary and three-way (worked by hand in test_calibration.py):
# binary κ = 23/35, prevalence-adjusted κ = 2/3, agreement 10/12; three-way κ = 15/31.
_AGREEMENT_TABLE = (
    [("include", "include")] * 3 + [("exclude", "exclude")] * 4 + [("uncertain", "include")] * 2
    + [("uncertain", "uncertain")] + [("include", "exclude")] + [("exclude", "uncertain")]
)


class TestMethodsAgreement:
    """The agreement sentences, on votes where the wrong pairs, categories or stage give another figure."""

    def _screened(self, project):
        sids = []
        for i, (ai, human) in enumerate(_AGREEMENT_TABLE):
            sid = _add_source(project, f"P{i}")
            vote(project.db, sid, ai, "gpt", stage="abstract", reviewer_type="ai")
            vote(project.db, sid, human, "amber", stage="abstract")
            sids.append(sid)
        return sids

    def test_kappa_is_binary_and_reported_with_its_interval_and_agreement(self, tmp_project):
        self._screened(tmp_project)
        lo, hi = cohen_kappa_ci(binarize(list(_AGREEMENT_TABLE)), categories=BINARY_CATEGORIES)
        text = build_methods_skeleton(tmp_project)
        assert ("Agreement between AI: gpt and amber on the 12 records both reviewers judged at title/abstract "
                f"screening was Cohen's κ = 0.66 (95% CI [{lo:.2f}, {hi:.2f}], Fleiss-Cohen-Everitt") in text
        assert "prevalence-adjusted κ = 0.67; percent agreement = 83.3%)" in text

    def test_full_text_votes_stay_out_of_the_title_abstract_figure(self, tmp_project):
        for sid in self._screened(tmp_project):
            vote(tmp_project.db, sid, "include", "gpt", stage="full_text", reviewer_type="ai")
            vote(tmp_project.db, sid, "exclude", "amber", stage="full_text")
        text = build_methods_skeleton(tmp_project)
        assert "judged at title/abstract screening was Cohen's κ = 0.66" in text
        assert "judged at full-text review was Cohen's κ = 0.00" in text

    def test_a_record_flagged_as_a_duplicate_leaves_the_agreement(self, tmp_project):
        """It counts as a duplicate removed rather than as screened, so not towards κ either."""
        sids = self._screened(tmp_project)
        tmp_project.db.mark_source_duplicate(sids[10], True)     # the pair that disagreed outright
        text = build_methods_skeleton(tmp_project)
        assert "on the 11 records both reviewers judged at title/abstract screening was Cohen's κ = 0.81" in text

    def test_further_reviewer_pairs_are_listed_with_their_own_figures(self, tmp_project):
        sids = self._screened(tmp_project)
        # bob shares five records: he agrees with amber on all five and with the AI on three
        for i, decision in ((0, "include"), (3, "exclude"), (4, "exclude"), (10, "exclude"), (11, "include")):
            vote(tmp_project.db, sids[i], decision, "bob", stage="abstract")
        text = build_methods_skeleton(tmp_project)
        assert "Agreement between AI: gpt and amber on the 12 records" in text
        assert "AI: gpt vs bob: κ = 0.17 (95% CI" in text and "amber vs bob: κ = 1.00 (95% CI" in text
        assert text.count(", n = 5)") == 2


class TestMethodsDesign:
    """Which design paragraphs are written, and the settings and records they cite."""

    def _ai_votes(self, project, *llm_params, stage="abstract"):
        for i, params in enumerate(llm_params):
            sid = _add_source(project, f"{stage} {i}")
            project.db.insert_screening_decision(ScreeningDecision(
                decision="include", reasoning="t", reviewer_type="ai", reviewer_id="gpt",
                source_id=sid, stage=stage, llm_params=params,
            ))

    def test_assisted_screening_names_the_recorded_model_and_settings(self, tmp_project):
        self._ai_votes(tmp_project, {"model": "claude-x", "temperature": 0.0, "seed": 7})
        assert "screened by claude-x (temperature 0.0, seed 7) and one human reviewer" in build_methods_skeleton(tmp_project)

    def test_each_recorded_configuration_is_named_with_its_count(self, tmp_project):
        a = {"model": "claude-a", "temperature": 0.0}
        self._ai_votes(tmp_project, a, a, a, {"model": "claude-b", "temperature": None})
        text = build_methods_skeleton(tmp_project)
        assert "claude-a (temperature 0.0, 3 rows) and claude-b (temperature not recorded, 1 rows)" in text

    def test_full_text_rows_are_not_title_abstract_settings(self, tmp_project):
        self._ai_votes(tmp_project, {"model": "claude-abstract", "temperature": 0.0})
        self._ai_votes(tmp_project, {"model": "claude-fulltext", "temperature": 0.5}, stage="full_text")
        text = build_methods_skeleton(tmp_project)
        assert "screened by claude-abstract (temperature 0.0) and one human reviewer" in text

    def test_with_nothing_recorded_the_configuration_is_cited_as_such(self, tmp_project):
        project = set_config(tmp_project, "llm", model="claude-config", temperature=0.3)
        text = build_methods_skeleton(project)
        assert "screened by claude-config (temperature 0.3, per the current configuration)" in text

    def test_independent_screening_reports_the_ai_as_a_reference_only(self, tmp_project):
        for i, decision in enumerate(["include"] * 3 + ["exclude"] * 2 + ["uncertain"]):
            vote(tmp_project.db, _add_source(tmp_project, f"P{i}"), decision, "gpt", stage="abstract", reviewer_type="ai")
        save_stage_workflow(tmp_project.root, "screening", "independent")
        text = build_methods_skeleton(Project(tmp_project.root))
        assert "additionally run as a reference reviewer (not counted as one of the two required reviewers)" in text
        assert "6 AI-screened records (3 include / 2 exclude / 1 uncertain)" in text

    def test_verify_extraction_and_whether_criteria_were_rechecked(self, tmp_project):
        text = build_methods_skeleton(tmp_project)
        assert "(AI-extract + human-verify design)" in text and "(enabled for this project)" in text
        assert "(disabled for this project)" in build_methods_skeleton(set_config(tmp_project, "extraction", flag_check=False))

    def test_registration_and_protocol(self, tmp_project):
        text = build_methods_skeleton(tmp_project)
        assert "This review was not registered." in text and "No protocol was prepared in advance." in text
        save_registration(tmp_project.root, "OSF", "10.17605/OSF.IO/ABCDE", "https://osf.io/abcde")
        text = build_methods_skeleton(Project(tmp_project.root))
        assert "This review was registered with OSF (10.17605/OSF.IO/ABCDE)." in text
        assert "The protocol is available at https://osf.io/abcde." in text

    def test_revisions_after_the_first_version_are_listed_as_amendments(self, tmp_project):
        db, pid = tmp_project.db, tmp_project.project_id
        db.save_artifact_version(pid, "criteria", '{"criteria": [{"id": "c1"}]}', "as first written")
        assert "The protocol was not amended after the review began." in build_methods_skeleton(tmp_project)
        db.save_artifact_version(pid, "criteria", '{"criteria": [{"id": "c1"}, {"id": "c2"}]}', "added c2")
        text = build_methods_skeleton(tmp_project)
        assert "1 amendment(s) were made to the protocol after its first version." in text
        assert "| Eligibility criteria | v2 |" in text and "| added c2 |" in text

    def test_llm_calls_and_tokens_are_totalled(self, tmp_project):
        text = build_methods_skeleton(tmp_project)
        assert "Total LLM calls" not in text and "not guaranteed to reproduce" not in text
        for n_in, n_out in ((1000, 200), (1500, 300)):
            tmp_project.db.insert_api_call(tmp_project.project_id, CallMetadata(
                provider="anthropic", model="claude-x", input_tokens=n_in, output_tokens=n_out))
        text = build_methods_skeleton(tmp_project)
        assert "Total LLM calls: 2. Total tokens (in + out): 3,000." in text
        assert "not guaranteed to reproduce identical model outputs" in text


class TestReportAndSvgShareCounts:
    def test_markdown_report_renders_the_counts(self, tmp_project):
        _pipeline_state(tmp_project)
        c = prisma_counts(tmp_project)
        report = build_prisma_report(tmp_project)
        assert f"**Total records identified:** {c['records_identified']}" in report
        assert f"**Records screened:** {c['abstract_screened']}" in report
        assert f"**Reports sought for retrieval:** {c['reports_sought']}" in report
        assert f"**Reports not retrieved:** {c['reports_not_retrieved']}" in report
        assert f"**Studies included:** {c['studies_included']}" in report

    def test_svg_renders_the_same_counts(self, tmp_project):
        """0.17 regression: diagram and report must come from one prisma_counts."""
        _pipeline_state(tmp_project)
        c = prisma_counts(tmp_project)
        svg = build_prisma_svg(tmp_project)
        assert f"{c['records_identified']} records identified" in svg
        assert f"{c['records_after_dedup']} records after duplicates removed" in svg
        assert f"{c['reports_sought']} reports sought for retrieval" in svg
        assert f"{c['full_text_assessed']} full-text studies assessed" in svg
        assert f"{c['studies_included']} studies included" in svg


def _distinct_counts(two_arms=False) -> dict:
    """Counts in which no two boxes share a number: totals 1xx, database arm 2xx, other arm 3xx."""
    def arm(base):
        keys = ("identified", "duplicates", "after_dedup", "screened", "excluded_abstract", "abstract_pending",
                "sought", "retrieved", "not_retrieved", "assessed", "excluded_full_text")
        out = {k: base + i for i, k in enumerate(keys, start=1)}
        out.update(full_text_exclusion_reasons=[{"reason": "Wrong population", "n": base + 12}],
                   full_text_pending=base + 13, included=base + 14, included_reports=base + 15)
        return out

    other = arm(300) if two_arms else {**arm(300), "identified": 0}
    keys = ("records_identified", "duplicates_removed", "duplicates_flagged", "records_after_dedup",
            "abstract_screened", "abstract_excluded", "abstract_pending", "ai_abstract_screened",
            "ai_abstract_included", "ai_abstract_excluded", "ai_abstract_uncertain", "reports_sought",
            "reports_retrieved", "reports_not_retrieved", "full_text_assessed", "full_text_excluded_reports")
    counts = {k: 100 + i for i, k in enumerate(keys, start=1)}
    counts.update(
        project_name="Distinct", project_type="systematic",
        by_source_database=[{"source_database": "PubMed", "n": 401}],
        by_route={"database": [{"source_database": "PubMed", "n": 401}],
                  "other": [{"source_database": "Citation searching", "n": 402}] if two_arms else []},
        database_arm=arm(200), other_arm=other,
        full_text_exclusion_reasons=[{"reason": "Wrong population", "n": 117}, {"reason": "Wrong design", "n": 118}],
        full_text_pending=119, studies_included=120, reports_included=121, studies_extracted=122,
    )
    return counts


def _svg_boxes(svg: str) -> list[list[str]]:
    return [re.findall(r"<tspan[^>]*>(.*?)</tspan>", text) for text in re.findall(r"<text[^>]*>(.*?)</text>", svg)]


def _numbers(text: str) -> set[int]:
    return {int(n) for n in re.findall(r"\b[1-4]\d\d\b", text)}


class TestEveryBoxShowsItsOwnNumber:
    """The renderers fed counts in which no two boxes are equal; on real data several boxes are often 1."""

    @pytest.fixture
    def counts(self, monkeypatch):
        def use(two_arms=False):
            c = _distinct_counts(two_arms)
            monkeypatch.setattr("ailr.exports.prisma.prisma_counts", lambda _project: c)
            return c
        return use

    def test_svg_single_column(self, tmp_project, counts):
        counts()
        assert _svg_boxes(build_prisma_svg(tmp_project)) == [
            ["101 records identified", "PubMed: 401"],
            ["102 duplicates removed"],
            ["104 records after duplicates removed"],
            ["106 excluded at title/abstract", "107 awaiting a decision"],
            ["112 reports sought for retrieval"],
            ["114 reports not retrieved"],
            ["115 full-text studies assessed"],
            ["116 excluded, with reasons:", "  Wrong population: 117", "  Wrong design: 118", "119 awaiting a decision"],
            ["120 studies included", "in 121 reports", "of which extracted: 122"],
        ]

    def test_svg_two_arms(self, tmp_project, counts):
        counts(two_arms=True)
        assert _svg_boxes(build_prisma_svg(tmp_project)) == [
            ["Via databases and registers", "201 records identified", "PubMed: 401"],
            ["202 duplicates removed"],
            ["203 records after duplicates removed"],
            ["205 excluded at title/abstract", "206 awaiting a decision"],
            ["207 reports sought for retrieval"],
            ["209 reports not retrieved"],
            ["210 full-text studies assessed"],
            ["211 excluded, with reasons:", "  Wrong population: 212", "213 awaiting a decision"],
            ["120 studies included", "in 121 reports", "of which extracted: 122"],
            ["Via other methods", "301 records identified", "Citation searching: 402"],
            ["307 reports sought for retrieval"],
            ["310 full-text studies assessed"],
        ]

    def test_markdown_report(self, tmp_project, counts):
        counts()
        report = build_prisma_report(tmp_project)
        sections = {part.split("\n", 1)[0]: part for part in report.split("\n## ")[1:]}
        assert _numbers(sections["Identification"]) == {101, 102, 104, 401}
        assert _numbers(sections["Screening (Title + Abstract)"]) == {105, 106, 107, 108, 109, 110, 111}
        assert _numbers(sections["Eligibility (Full Text)"]) == {112, 114, 115, 116, 117, 118, 119}
        assert _numbers(sections["Included"]) == {120, 121, 122}
        for line in ("**Total records identified:** 101", "**Duplicates removed:** 102 ", "**Records after deduplication:** 104",
                     "**Records screened:** 105", "- excluded: 106", "- awaiting a decision: 107", "108 screened",
                     "include 109, exclude 110, uncertain 111", "**Reports sought for retrieval:** 112",
                     "**Reports not retrieved:** 114", "**Reports assessed for eligibility:** 115",
                     "- awaiting a decision: 119", "**Full-text reports excluded, with reasons:** 116",
                     "- Wrong population: 117", "- Wrong design: 118", "**Studies included:** 120",
                     "**Reports of included studies:** 121", "- with completed extraction: 122"):
            assert line in report, line
        assert "fuzzy" not in report

    def test_markdown_report_two_arms(self, tmp_project, counts):
        counts(two_arms=True)
        report = build_prisma_report(tmp_project)
        identification = report.split("## Identification")[1].split("## Screening")[0]
        databases, other = identification.split("### Via other methods")
        assert "- PubMed: 401" in databases and "**Records identified:** 201" in databases
        assert "- Citation searching: 402" in other and "**Records identified:** 301" in other
        assert [line for line in report.splitlines() if line.startswith("| ") and "Other methods" not in line] == [
            "| Records identified | 201 | 301 |",
            "| Duplicates removed | 202 | 302 |",
            "| Records after duplicates removed | 203 | 303 |",
            "| Records screened | 204 | 304 |",
            "| Reports sought for retrieval | 207 | 307 |",
            "| Reports assessed for eligibility | 210 | 310 |",
            "| Studies included | 214 | 314 |",
        ]

    def test_reports_page(self, counts):
        text = component_text(reports_view._prisma_diagram(counts()))
        expected = ["101 records identified", "PubMed: 401", "102 duplicates removed before screening",
                    "104 records after duplicates removed", "106 studies excluded at title/abstract",
                    "107 awaiting a decision", "112 reports sought for retrieval",
                    "114 reports not retrieved (no full text)", "115 full-text studies assessed for eligibility",
                    "116 studies excluded, with reasons:", "Wrong population: 117", "Wrong design: 118",
                    "119 awaiting a decision", "120 studies included"]
        found = [text.find(s) for s in expected]
        assert -1 not in found and found == sorted(found), list(zip(expected, found))

    def test_reports_page_two_arms(self, counts):
        text = component_text(reports_view._prisma_diagram(counts(two_arms=True)))
        for s in ("101 records identified", "Via databases and registers: 201", "Via other methods: 301",
                  "Citation searching: 402", "104 records after duplicates removed", "Records identified 301",
                  "Duplicates removed 302", "Reports sought for retrieval 307",
                  "Reports assessed for eligibility 310", "Studies included 314"):
            assert s in text, s


class TestReportText:
    """Claims the Markdown report makes about itself, rather than the counts inside it."""

    def test_the_footer_names_the_checklist_the_review_type_implies(self, tmp_project):
        """A scoping review must not claim PRISMA 2020 conformance, nor the other way round."""
        _add_source(tmp_project, "S1")
        scoping = build_prisma_report(tmp_project)      # 'scoping' is the Project.init default
        assert "PRISMA-ScR" in scoping and "PRISMA 2020" not in scoping

        save_project_type(tmp_project.root, "systematic")
        systematic = build_prisma_report(Project(tmp_project.root))
        assert "PRISMA 2020" in systematic and "PRISMA-ScR" not in systematic

    def test_reports_are_named_separately_only_when_they_outnumber_the_studies(self, tmp_project):
        """PRISMA's included box wants studies and reports both, but the second line is noise
        until companion reports have actually been grouped."""
        db = tmp_project.db
        primary = _add_source(tmp_project, "primary report", with_md=True)
        companion = _add_source(tmp_project, "companion report", with_md=True)
        for sid in (primary, companion):
            vote(db, sid, "include", "amber", stage="abstract")
            vote(db, sid, "include", "amber", stage="full_text")
        assert "**Reports of included studies:**" not in build_prisma_report(tmp_project)

        db.set_study_group(companion, primary)
        c = prisma_counts(tmp_project)
        assert (c["studies_included"], c["reports_included"]) == (1, 2)
        assert "**Reports of included studies:** 2" in build_prisma_report(tmp_project)

    def test_records_awaiting_a_decision_are_shown_only_while_there_are_some(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, "in conflict")
        vote(db, sid, "include", "gpt", stage="abstract", reviewer_type="ai")
        vote(db, sid, "exclude", "amber", stage="abstract")
        assert "- awaiting a decision: 1" in build_prisma_report(tmp_project)
        assert "1 awaiting a decision" in build_prisma_svg(tmp_project)

        db.insert_screening_reconciliation(sid, "exclude", "pi", "", stage="abstract")
        assert "awaiting a decision" not in build_prisma_report(tmp_project)
        assert "awaiting a decision" not in build_prisma_svg(tmp_project)


class TestAgreementReporting:
    def test_kappa_uses_latest_only_pairs(self, tmp_project):
        _pipeline_state(tmp_project)
        text = build_methods_skeleton(tmp_project)
        # s1: (include, include); s2: (exclude, exclude) after the re-vote -> perfect agreement
        assert "Agreement between AI: gpt and amber on the 2 records" in text
        assert "κ = 1.00" in text

    def test_agreement_pairs_per_reviewer_not_latest_human(self, tmp_project):
        """Two humans on one paper used to collapse to 'the latest human vote', so the AI was
        compared against whoever happened to vote last. Each reviewer is now its own rater."""
        db = tmp_project.db
        sid = _add_source(tmp_project, "two humans plus AI")
        vote(db, sid, "include", "amber", stage="abstract")
        vote(db, sid, "exclude", "lin", stage="abstract")
        vote(db, sid, "include", "gpt", stage="abstract", reviewer_type="ai")

        rows = db.latest_decisions_by_rater(tmp_project.project_id)
        assert sorted(r["rater"] for r in rows) == ["AI: gpt", "amber", "lin"]
        assert decisions_for_pair(rows, "AI: gpt", "amber") == [("include", "include")]
        assert decisions_for_pair(rows, "AI: gpt", "lin") == [("include", "exclude")]
        assert decisions_for_pair(rows, "amber", "lin") == [("include", "exclude")]
        assert [(a, b, n) for a, b, n in rater_overlaps(rows)] == [
            ("AI: gpt", "amber", 1), ("AI: gpt", "lin", 1), ("amber", "lin", 1),
        ]

    def test_agreement_reads_votes_before_reconciliation(self, tmp_project):
        """Reliability describes agreement as cast; adjudicating a conflict must not rewrite it."""
        db = tmp_project.db
        sid = _add_source(tmp_project, "reconciled disagreement")
        vote(db, sid, "include", "amber", stage="abstract")
        vote(db, sid, "exclude", "lin", stage="abstract")
        db.insert_screening_reconciliation(sid, "include", "amber", "discussed", stage="abstract")

        rows = db.latest_decisions_by_rater(tmp_project.project_id)
        assert decisions_for_pair(rows, "amber", "lin") == [("include", "exclude")]

    def test_the_binary_reliability_view_shows_the_three_way_kappa_beside_it(self, tmp_project):
        db = tmp_project.db
        for title, (amber, lin) in {"P1": ("uncertain", "include"), "P2": ("exclude", "exclude")}.items():
            sid = _add_source(tmp_project, title)
            vote(db, sid, amber, "amber", stage="abstract")
            vote(db, sid, lin, "lin", stage="abstract")
        rows = db.latest_decisions_by_rater(tmp_project.project_id, "abstract")

        text = component_text(reports_view._reliability_body(rows, json.dumps(["amber", "lin"]), "binary"))
        assert "Cohen's κ (95% CI): 1.0" in text
        assert "Three-way κ (stricter): 0.333" in text

    def test_uncertain_counts_as_include_when_binarized(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, "uncertain vs include")
        vote(db, sid, "uncertain", "amber", stage="abstract")
        vote(db, sid, "include", "lin", stage="abstract")

        rows = db.latest_decisions_by_rater(tmp_project.project_id)
        assert binarize(decisions_for_pair(rows, "amber", "lin")) == [("include", "include")]

    def test_builds_on_an_empty_project(self, tmp_project):
        text = build_methods_skeleton(tmp_project)
        assert text.startswith("# Methods")
        # the sections still render off zero counts rather than the header alone coming back
        assert "exact DOI match" in text
        assert "Full texts" in text


class TestFullTextExclusionReasons:
    """PRISMA wants each reason counted, and the total to be reports rather than votes."""

    def _excluded(self, project, title, reason, second=None, reconcile=None):
        db = project.db
        sid = _add_source(project, title)
        vote(db, sid, "exclude", "amber", stage="full_text", reasoning=reason)
        if second:
            vote(db, sid, second, "lin", stage="full_text")
        if reconcile:
            db.insert_screening_reconciliation(sid, reconcile, adjudicator="pi", stage="full_text")
        return sid

    def test_a_multi_reason_exclusion_counts_under_each_reason(self, tmp_project):
        db = tmp_project.db
        for name in ("Wrong population", "No full text"):
            db.create_exclusion_reason(tmp_project.project_id, name)
        self._excluded(tmp_project, "A", "Wrong population")
        self._excluded(tmp_project, "B", "Wrong population; No full text")
        counts = {r["reason"]: r["n"] for r in db.full_text_exclusion_counts(tmp_project.project_id, workflow="assisted")}
        assert counts == {"Wrong population": 2, "No full text": 1}
        # ...but the reports box counts reports, so it stays at 2, not 3.
        assert db.count_full_text_excluded_reports(tmp_project.project_id, workflow="assisted") == 2

    def test_free_text_with_a_semicolon_is_not_split(self, tmp_project):
        db = tmp_project.db
        db.create_exclusion_reason(tmp_project.project_id, "Wrong population")
        self._excluded(tmp_project, "A", "a note; with a semicolon")
        counts = {r["reason"]: r["n"] for r in db.full_text_exclusion_counts(tmp_project.project_id, workflow="assisted")}
        assert counts == {"a note; with a semicolon": 1}

    def test_two_reviewers_excluding_one_report_count_it_once(self, tmp_project):
        db = tmp_project.db
        db.create_exclusion_reason(tmp_project.project_id, "Wrong population")
        self._excluded(tmp_project, "A", "Wrong population", second="exclude")
        assert db.count_full_text_excluded_reports(tmp_project.project_id, workflow="assisted") == 1

    def test_a_disagreement_reconciled_to_include_is_not_excluded(self, tmp_project):
        db = tmp_project.db
        db.create_exclusion_reason(tmp_project.project_id, "Wrong population")
        self._excluded(tmp_project, "A", "Wrong population", second="include", reconcile="include")
        assert db.count_full_text_excluded_reports(tmp_project.project_id, workflow="assisted") == 0
        assert db.full_text_exclusion_counts(tmp_project.project_id, workflow="assisted") == []
        assert db.count_final_includes(tmp_project.project_id, "full_text", workflow="assisted") == 1


class TestIdentificationArms:
    """PRISMA 2020 splits identification into databases/registers and other methods."""

    def _add(self, project, title, route, db_name, abstract="include", ft=None, md=False):
        db = project.db
        sid = db.insert_source(Source(title=title, project_id=project.project_id,
                                      source_database=db_name, identification_route=route))
        if md:
            db.update_markdown_path(sid, Path("data/markdown") / f"{sid}.md")
        vote(db, sid, abstract, "amber", stage="abstract")
        if ft:
            vote(db, sid, ft, "amber", stage="full_text")
        return sid

    def test_records_default_to_the_database_arm(self, tmp_project):
        _add_source(tmp_project, "no route given")
        c = prisma_counts(tmp_project)
        assert c["database_arm"]["identified"] == 1
        assert c["other_arm"]["identified"] == 0

    def test_arms_are_counted_separately_and_sum_to_the_total(self, tmp_project):
        self._add(tmp_project, "db1", "database", "PubMed", md=True, ft="include")
        self._add(tmp_project, "db2", "database", "PubMed", abstract="exclude")
        self._add(tmp_project, "cit1", "other", "Citation searching", md=True, ft="include")
        c = prisma_counts(tmp_project)
        assert c["database_arm"]["identified"] == 2
        assert c["other_arm"]["identified"] == 1
        assert c["database_arm"]["included"] + c["other_arm"]["included"] == c["studies_included"] == 2
        assert c["by_route"]["other"] == [{"source_database": "Citation searching", "n": 1}]

    def test_each_arm_splits_retrieved_from_not_retrieved(self, tmp_project):
        """The project-level split is covered above; the per-arm one runs through _arm_counts,
        which had no test of its own."""
        db = tmp_project.db
        self._add(tmp_project, "db-retrieved", "database", "PubMed")
        db_missing = self._add(tmp_project, "db-missing", "database", "PubMed")
        other_missing = self._add(tmp_project, "cit-missing", "other", "Citation searching")
        db.set_full_text_not_retrieved(db_missing, True)
        db.set_full_text_not_retrieved(other_missing, True)

        c = prisma_counts(tmp_project)
        db_arm, other_arm = c["database_arm"], c["other_arm"]
        assert (db_arm["sought"], db_arm["retrieved"], db_arm["not_retrieved"]) == (2, 1, 1)
        assert (other_arm["sought"], other_arm["retrieved"], other_arm["not_retrieved"]) == (1, 0, 1)

    def test_the_diagram_stays_single_column_without_other_sources(self, tmp_project):
        self._add(tmp_project, "db1", "database", "PubMed")
        svg = build_prisma_svg(tmp_project)
        report = build_prisma_report(tmp_project)
        assert "Via other methods" not in svg
        assert "Via databases and registers" not in report

    def test_both_arms_are_drawn_when_other_sources_exist(self, tmp_project):
        self._add(tmp_project, "db1", "database", "PubMed")
        self._add(tmp_project, "cit1", "other", "Citation searching")
        svg = build_prisma_svg(tmp_project)
        report = build_prisma_report(tmp_project)
        assert "Via other methods" in svg and "Via databases and registers" in svg
        assert "### Via other methods" in report

    def _two_arms_with_duplicates(self, project):
        """4 database records (one later flagged by hand as a duplicate), 1 record from citation
        searching, and 2 citation-search records dropped at import as duplicates."""
        db = project.db
        for i in range(3):
            self._add(project, f"db{i}", "database", "PubMed")
        db.mark_source_duplicate(self._add(project, "db copy", "database", "PubMed"), True)
        self._add(project, "cit1", "other", "Citation searching")
        for i in range(2):
            record = {"title": f"cit dup {i}", "identification_route": "other"}
            db.insert_duplicate(project.project_id, record["title"], None, "doi", full_record_json=json.dumps(record))

    def test_each_arm_counts_its_records_before_deduplication(self, tmp_project):
        self._two_arms_with_duplicates(tmp_project)
        c = prisma_counts(tmp_project)
        db_arm, other = c["database_arm"], c["other_arm"]
        assert (db_arm["identified"], other["identified"]) == (4, 3)
        assert (db_arm["duplicates"], db_arm["after_dedup"]) == (1, 3)
        assert (other["duplicates"], other["after_dedup"]) == (2, 1)
        assert (c["records_identified"], c["duplicates_removed"], c["records_after_dedup"]) == (7, 3, 4)

    def test_a_duplicate_stashed_before_routes_were_recorded_counts_for_databases(self, tmp_project):
        self._add(tmp_project, "db1", "database", "PubMed")
        self._add(tmp_project, "cit1", "other", "Citation searching")
        tmp_project.db.insert_duplicate(tmp_project.project_id, "old dup", None, "doi")
        c = prisma_counts(tmp_project)
        assert (c["database_arm"]["identified"], c["other_arm"]["identified"]) == (2, 1)

    def test_the_three_renderings_agree_on_the_two_arm_numbers(self, tmp_project):
        self._two_arms_with_duplicates(tmp_project)
        svg = build_prisma_svg(tmp_project)
        assert "4 records identified" in svg and "3 records after duplicates removed" in svg

        report = build_prisma_report(tmp_project)
        assert "**Records identified:** 4" in report and "**Records identified:** 3" in report
        assert "**Records after deduplication:** 4" in report

        tab = component_text(reports_view._prisma_diagram(prisma_counts(tmp_project)))
        assert "7 records identified" in tab and "4 records after duplicates removed" in tab
