"""PRISMA / report numbers — the counts that end up in the manuscript.

Regressions guarded:
- 0.17: stage counts use latest-only decisions (a re-vote must not inflate 'records screened')
- 0.17: the Markdown report and the SVG diagram share ONE set of counts (they used to disagree)
- methods skeleton reports κ over the same latest-only pairing as Reports
"""

from pathlib import Path

from ailr.core.config import save_stage_workflow
from ailr.core.project import Project
from ailr.core.source import Source
from ailr.exports.methods import build_methods_skeleton
from ailr.exports.prisma import build_prisma_report, build_prisma_svg, prisma_counts
from ailr.ingest.dedup import TITLE_MATCH_THRESHOLD
from ailr.metrics import binarize, decisions_for_pair, rater_overlaps
from ailr.reviewers import ExtractionResult, ScreeningDecision


def _add_source(project, title, with_md=False):
    sid = project.db.insert_source(Source(title=title, project_id=project.project_id, source_database="test-db"))
    if with_md:
        project.db.update_markdown_path(sid, Path("data/markdown") / f"{sid}.md")
    return sid


def _vote(db, sid, decision, reviewer_id, reviewer_type="human", stage="abstract", reasoning="test"):
    db.insert_screening_decision(ScreeningDecision(
        decision=decision, reasoning=reasoning, reviewer_type=reviewer_type,
        reviewer_id=reviewer_id, source_id=sid, stage=stage,
    ))


def _pipeline_state(project):
    """4 sources + 1 dropped duplicate; s1 fully through the pipeline, s2 excluded after a
    re-vote, s3 included but PDF never retrieved, s4 untouched."""
    db = project.db
    s1 = _add_source(project, "S1 full pipeline", with_md=True)
    s2 = _add_source(project, "S2 excluded on revote")
    s3 = _add_source(project, "S3 include without pdf")
    _add_source(project, "S4 unscreened")
    db.insert_duplicate(project.project_id, "dropped dup", None, "doi")

    _vote(db, s1, "include", "amber")
    _vote(db, s2, "include", "amber")
    _vote(db, s2, "exclude", "amber")   # re-vote: only the latest may count
    _vote(db, s3, "include", "amber")
    _vote(db, s1, "include", "gpt", reviewer_type="ai")
    _vote(db, s2, "exclude", "gpt", reviewer_type="ai")

    _vote(db, s1, "include", "amber", stage="full_text")
    db.insert_extraction(ExtractionResult(
        extractor_type="ai", extractor_id="gpt", field_name="design", value="obs", source_id=s1,
    ))
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
        assert c["studies_included"] == 1
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
        _vote(db, sid, "include", "amber")
        _vote(db, sid, "include", "bob")
        _vote(db, sid, "include", "amber", stage="full_text")
        _vote(db, sid, "include", "bob", stage="full_text")
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
        _vote(db, s_out, "include", "amber", stage="full_text")
        db.insert_screening_reconciliation(s_out, "exclude", adjudicator="pi", stage="full_text")
        s_in = _add_source(tmp_project, "excluded then adjudicated in", with_md=True)
        _vote(db, s_in, "exclude", "amber", stage="full_text")
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
        _vote(db, sid, "include", "amber")
        _vote(db, sid, "include", "bob")
        _vote(db, sid, "include", "amber", stage="full_text")
        save_stage_workflow(tmp_project.root, "screening", "independent")

        project = self._reload(tmp_project)
        assert prisma_counts(project)["reports_sought"] == 1
        assert prisma_counts(project)["studies_included"] == 0

        _vote(project.db, sid, "include", "bob", stage="full_text")
        assert prisma_counts(project)["studies_included"] == 1

    def test_the_full_text_override_gates_the_included_box(self, tmp_project):
        """Abstract independent, full text assisted: two abstract votes, but one full-text vote
        settles the stage."""
        db = tmp_project.db
        sid = _add_source(tmp_project, "assisted at full text", with_md=True)
        _vote(db, sid, "include", "amber")
        _vote(db, sid, "include", "bob")
        _vote(db, sid, "include", "amber", stage="full_text")
        save_stage_workflow(tmp_project.root, "screening", "independent")
        save_stage_workflow(tmp_project.root, "full_text_screening", "assisted")

        assert prisma_counts(self._reload(tmp_project))["studies_included"] == 1

    def test_a_half_screened_paper_is_not_yet_sought(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, "one abstract vote", with_md=True)
        _vote(db, sid, "include", "amber")
        save_stage_workflow(tmp_project.root, "screening", "independent")

        c = prisma_counts(self._reload(tmp_project))
        assert c["abstract_screened"] == 1   # it has been looked at
        assert c["reports_sought"] == 0      # but the stage is not finished with it

    def test_an_unresolved_disagreement_counts_as_neither(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, "unresolved", with_md=True)
        _vote(db, sid, "include", "amber")
        _vote(db, sid, "exclude", "bob")
        save_stage_workflow(tmp_project.root, "screening", "independent")

        c = prisma_counts(self._reload(tmp_project))
        assert c["reports_sought"] == 0
        assert c["studies_included"] == 0


class TestMethodsSkeleton:
    """A placeholder: the methods text is prose that changes often, so this only checks it
    reports the dedup threshold actually in force and names the full-text design."""

    def test_reports_the_real_dedup_threshold(self, tmp_project):
        _add_source(tmp_project, "S1")
        text = build_methods_skeleton(tmp_project)
        assert f"threshold = {TITLE_MATCH_THRESHOLD}" in text

    def test_describes_the_full_text_design(self, tmp_project):
        _add_source(tmp_project, "S1")
        save_stage_workflow(tmp_project.root, "full_text_screening", "independent")

        text = build_methods_skeleton(Project(tmp_project.root))
        assert "Full texts" in text
        assert "independently by two human" in text


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
        _vote(db, sid, "include", "amber")
        _vote(db, sid, "exclude", "lin")
        _vote(db, sid, "include", "gpt", reviewer_type="ai")

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
        _vote(db, sid, "include", "amber")
        _vote(db, sid, "exclude", "lin")
        db.insert_screening_reconciliation(sid, "include", "amber", "discussed", stage="abstract")

        rows = db.latest_decisions_by_rater(tmp_project.project_id)
        assert decisions_for_pair(rows, "amber", "lin") == [("include", "exclude")]

    def test_uncertain_counts_as_include_when_binarized(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, "uncertain vs include")
        _vote(db, sid, "uncertain", "amber")
        _vote(db, sid, "include", "lin")

        rows = db.latest_decisions_by_rater(tmp_project.project_id)
        assert binarize(decisions_for_pair(rows, "amber", "lin")) == [("include", "include")]

    def test_builds_on_an_empty_project(self, tmp_project):
        text = build_methods_skeleton(tmp_project)
        assert text.startswith("# Methods")


class TestFullTextExclusionReasons:
    """PRISMA wants each reason counted, and the total to be reports rather than votes."""

    def _excluded(self, project, title, reason, second=None, reconcile=None):
        db = project.db
        sid = _add_source(project, title)
        _vote(db, sid, "exclude", "amber", stage="full_text", reasoning=reason)
        if second:
            _vote(db, sid, second, "lin", stage="full_text")
        if reconcile:
            db.insert_screening_reconciliation(sid, reconcile, adjudicator="pi", stage="full_text")
        return sid

    def test_a_multi_reason_exclusion_counts_under_each_reason(self, tmp_project):
        db = tmp_project.db
        for name in ("Wrong population", "No full text"):
            db.create_exclusion_reason(tmp_project.project_id, name)
        self._excluded(tmp_project, "A", "Wrong population")
        self._excluded(tmp_project, "B", "Wrong population; No full text")
        counts = {r["reason"]: r["n"] for r in db.full_text_exclusion_counts(tmp_project.project_id)}
        assert counts == {"Wrong population": 2, "No full text": 1}
        # ...but the reports box counts reports, so it stays at 2, not 3.
        assert db.count_full_text_excluded_reports(tmp_project.project_id) == 2

    def test_free_text_with_a_semicolon_is_not_split(self, tmp_project):
        db = tmp_project.db
        db.create_exclusion_reason(tmp_project.project_id, "Wrong population")
        self._excluded(tmp_project, "A", "a note; with a semicolon")
        counts = {r["reason"]: r["n"] for r in db.full_text_exclusion_counts(tmp_project.project_id)}
        assert counts == {"a note; with a semicolon": 1}

    def test_two_reviewers_excluding_one_report_count_it_once(self, tmp_project):
        db = tmp_project.db
        db.create_exclusion_reason(tmp_project.project_id, "Wrong population")
        self._excluded(tmp_project, "A", "Wrong population", second="exclude")
        assert db.count_full_text_excluded_reports(tmp_project.project_id) == 1

    def test_a_disagreement_reconciled_to_include_is_not_excluded(self, tmp_project):
        db = tmp_project.db
        db.create_exclusion_reason(tmp_project.project_id, "Wrong population")
        self._excluded(tmp_project, "A", "Wrong population", second="include", reconcile="include")
        assert db.count_full_text_excluded_reports(tmp_project.project_id) == 0
        assert db.full_text_exclusion_counts(tmp_project.project_id) == []
        assert db.count_final_includes(tmp_project.project_id, "full_text", workflow="assisted") == 1


class TestIdentificationArms:
    """PRISMA 2020 splits identification into databases/registers and other methods."""

    def _add(self, project, title, route, db_name, abstract="include", ft=None, md=False):
        db = project.db
        sid = db.insert_source(Source(title=title, project_id=project.project_id,
                                      source_database=db_name, identification_route=route))
        if md:
            db.update_markdown_path(sid, Path("data/markdown") / f"{sid}.md")
        _vote(db, sid, abstract, "amber")
        if ft:
            _vote(db, sid, ft, "amber", stage="full_text")
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
