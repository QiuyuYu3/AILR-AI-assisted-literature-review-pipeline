"""Agreement metrics and the calibration pairing.

The pairing regression (0.24): κ must compare only the LATEST decision per reviewer type
at the ABSTRACT stage — full-text decisions and superseded re-votes used to skew it.
"""

import math

from ailr.core.source import Source
from ailr.metrics import (
    BINARY_CATEGORIES,
    THREE_WAY_CATEGORIES,
    binarize,
    cohen_kappa,
    cohen_kappa_ci,
    confusion_matrix,
    pabak,
    percent_agreement,
    rater_overlaps,
)
from ailr.reviewers import ScreeningDecision
from ailr.tasks.calibrate import (
    CalibrationSummary,
    CalibrationTask,
    _agreement_stats,
    _latest_by_reviewer_type,
    quick_test_agreement,
)
from ailr.ui import calibration_view

# 12 AI/human pairs on which the two readings differ, worked by hand: binary p_o = 10/12,
# p_e = 74/144, κ = 23/35; three-way p_o = 8/12, p_e = 51/144, κ = 15/31.
_HAND_TABLE = (
    [("include", "include")] * 3 + [("exclude", "exclude")] * 4 + [("uncertain", "include")] * 2
    + [("uncertain", "uncertain")] + [("include", "exclude")] + [("exclude", "uncertain")]
)


def _pairs(matrix, categories):
    """Rows are rater 1, columns rater 2, so the R matrices below transcribe one-for-one."""
    return [(categories[i], categories[j])
            for i, row in enumerate(matrix)
            for j, count in enumerate(row)
            for _ in range(count)]


def _component_text(node) -> str:
    """Visible text of a Dash component tree, whitespace-normalised."""
    parts: list[str] = []

    def walk(x):
        if isinstance(x, str):
            parts.append(x)
            return
        children = getattr(x, "children", None)
        for child in children if isinstance(children, (list, tuple)) else [children]:
            if child is not None:
                walk(child)

    walk(node)
    return " ".join(" ".join(parts).split())


class TestCalibrationReadsKappaLikeTheManuscript:
    """The calibration κ is the figure the manuscript reports (uncertain counts as include), so the
    target a prompt is tuned against is the number that gets published."""

    def test_agreement_stats_use_the_binary_reading_and_carry_the_three_way_one(self):
        stats = _agreement_stats({sid: {"ai": ai, "human": h} for sid, (ai, h) in enumerate(_HAND_TABLE, 1)})
        assert math.isclose(stats["kappa"], 23 / 35)
        assert math.isclose(stats["agreement"], 10 / 12)
        assert math.isclose(stats["kappa_three_way"], 15 / 31)
        assert math.isclose(stats["agreement_three_way"], 8 / 12)


class TestMetrics:
    def test_kappa_perfect_agreement(self):
        pairs = [("include", "include")] * 3 + [("exclude", "exclude")] * 3
        assert cohen_kappa(pairs) == 1.0

    def test_kappa_chance_level_is_zero(self):
        # p_o = 0.5 and p_e = 0.5 -> kappa exactly 0
        pairs = [("include", "include"), ("exclude", "exclude"),
                 ("include", "exclude"), ("exclude", "include")]
        assert cohen_kappa(pairs) == 0.0

    def test_kappa_no_pairs_is_nan(self):
        assert math.isnan(cohen_kappa([]))

    def test_kappa_ignores_pairs_outside_categories(self):
        # Two surviving pairs, not one: a lone include/include pair puts every record in the same
        # category, which is the degenerate table below rather than a test of the filtering.
        pairs = [("include", "include"), ("exclude", "exclude"), ("weird", "include")]
        cats = ["include", "exclude", "uncertain"]
        kept = [("include", "include"), ("exclude", "exclude")]
        assert cohen_kappa(pairs, categories=cats) == cohen_kappa(kept, categories=cats) == 1.0

    def test_kappa_of_a_single_disagreeing_pair_is_zero(self):
        # n == 1 with a real answer, which the degenerate case below cannot provide.
        assert cohen_kappa([("include", "exclude")], categories=BINARY_CATEGORIES) == 0.0

    def test_kappa_is_undefined_when_every_record_is_one_category(self):
        """p_e = 1 makes κ a 0/0. cohen_kappa_ci reports the interval undefined, so the point
        estimate says undefined too rather than claiming perfect agreement."""
        assert math.isnan(cohen_kappa([("include", "include")] * 5, categories=BINARY_CATEGORIES))
        assert math.isnan(cohen_kappa_ci([("include", "include")] * 5, categories=BINARY_CATEGORIES)[0])

    def test_kappa_ci_is_undefined_when_there_are_no_pairs(self):
        lo, hi = cohen_kappa_ci([])
        assert math.isnan(lo) and math.isnan(hi)

    def test_perfect_agreement_over_two_categories_has_a_zero_width_interval(self):
        """Distinct from the degenerate one-category table above: with both categories present
        the variance is exactly 0, so the interval collapses onto κ instead of being undefined."""
        pairs = [("include", "include")] * 3 + [("exclude", "exclude")] * 3
        assert cohen_kappa_ci(pairs, categories=BINARY_CATEGORIES) == (1.0, 1.0)

    def test_kappa_ci_matches_r_vcd_on_an_unbalanced_table(self):
        """Golden values from R 4.5.2 / vcd 1.4-14, whose asymptotic variance the docstring claims:
        Kappa(matrix(c(8,2,3,7), nrow=2, byrow=TRUE))$Unweighted gives ASE 0.192678488679977,
        and confint() on it is exactly the kappa +- 1.959964 * ASE built here."""
        lo, hi = cohen_kappa_ci(_pairs([[8, 2], [3, 7]], BINARY_CATEGORIES),
                                categories=BINARY_CATEGORIES)
        assert round(lo, 12) == round(0.122357098612838, 12)
        assert round(hi, 12) == round(0.877642901387162, 12)

    def test_kappa_ci_matches_r_vcd_on_three_categories(self):
        """Same source, matrix(c(10,2,1, 3,15,2, 1,4,5), nrow=3, byrow=TRUE) in THREE_WAY order:
        kappa 0.520994001713796, ASE 0.109835499788421. Two categories leave the k-category sums
        in the variance untested."""
        pairs = _pairs([[10, 2, 1], [3, 15, 2], [1, 4, 5]], THREE_WAY_CATEGORIES)
        assert round(cohen_kappa(pairs, categories=THREE_WAY_CATEGORIES), 12) == round(0.520994001713796, 12)
        lo, hi = cohen_kappa_ci(pairs, categories=THREE_WAY_CATEGORIES)
        assert round(lo, 12) == round(0.305720376206482, 12)
        assert round(hi, 12) == round(0.736267627221110, 12)

    def test_a_degenerate_marginal_gives_a_zero_width_interval_not_float_noise(self):
        """19 agreed excludes + 1 disagreement, the realistic screening shape: one rater used a
        single category, so the variance cancels to exactly 0. The subtraction leaves noise on
        either side of 0 (R reports ASE = NaN on this table), which must not become an interval."""
        pairs = [("exclude", "exclude")] * 19 + [("include", "exclude")]
        assert cohen_kappa(pairs, categories=BINARY_CATEGORIES) == 0.0
        assert cohen_kappa_ci(pairs, categories=BINARY_CATEGORIES) == (0.0, 0.0)

    def test_percent_agreement(self):
        assert percent_agreement([("a", "a"), ("a", "b")]) == 0.5
        assert math.isnan(percent_agreement([]))

    def test_pabak_is_two_po_minus_one_for_two_categories(self):
        pairs = [("include", "include")] + [("exclude", "exclude")] * 3
        assert pabak(pairs, categories=BINARY_CATEGORIES) == 1.0
        assert pabak([("include", "exclude"), ("exclude", "exclude")], categories=BINARY_CATEGORIES) == 0.0
        assert math.isnan(pabak([]))

    def test_pabak_holds_up_where_kappa_collapses(self):
        """19 agreed excludes + 1 disagreement: 95% agreement, but κ is 0 because one category
        dominates. PABAK is what makes that readable."""
        pairs = [("exclude", "exclude")] * 19 + [("include", "exclude")]
        assert cohen_kappa(pairs, categories=BINARY_CATEGORIES) == 0.0
        assert round(pabak(pairs, categories=BINARY_CATEGORIES), 10) == 0.9

    def test_pabak_generalises_past_two_categories(self):
        """(k * p_o - 1) / (k - 1): at k=2 the denominator is 1, so every test that passes
        BINARY_CATEGORIES leaves the k-category generalisation unverified."""
        pairs = [("include", "include"), ("exclude", "exclude"), ("uncertain", "include")]
        assert round(percent_agreement(pairs), 10) == round(2 / 3, 10)
        assert round(pabak(pairs, categories=THREE_WAY_CATEGORIES), 10) == 0.5
        assert round(pabak(pairs, categories=BINARY_CATEGORIES), 10) == round(1 / 3, 10)

    def test_pabak_infers_the_categories_from_the_pairs(self):
        pairs = [("include", "include"), ("exclude", "exclude"), ("uncertain", "include")]
        assert pabak(pairs) == pabak(pairs, categories=THREE_WAY_CATEGORIES)

    def test_rater_overlaps_breaks_equal_counts_by_name(self):
        """methods.py reports overlaps[0] as the headline pair, so ties have to resolve the same
        way every run rather than following dict insertion order."""
        rows = [
            {"source_id": s, "rater": r, "decision": "include", "reviewer_type": "human"}
            for s, pair in ((1, ("amber", "zoe")), (2, ("amber", "bo")), (3, ("bo", "cy")))
            for r in pair
        ]
        assert rater_overlaps(rows) == [("amber", "bo", 1), ("amber", "zoe", 1), ("bo", "cy", 1)]

    def test_binarize_folds_uncertain_into_include(self):
        assert binarize([("uncertain", "include"), ("exclude", "uncertain")]) == [
            ("include", "include"), ("exclude", "include"),
        ]

    def test_confusion_matrix_counts(self):
        cats, m = confusion_matrix(
            [("include", "exclude"), ("include", "exclude"), ("exclude", "exclude")],
            categories=["include", "exclude"],
        )
        assert cats == ["include", "exclude"]
        assert m == [[0, 2], [0, 1]]


def _add_source(project, title="Paper"):
    return project.db.insert_source(Source(title=title, project_id=project.project_id))


def _vote(db, sid, decision, reviewer_id, reviewer_type, stage="abstract"):
    db.insert_screening_decision(ScreeningDecision(
        decision=decision, reasoning="test", reviewer_type=reviewer_type,
        reviewer_id=reviewer_id, source_id=sid, stage=stage,
    ))


def _agreement(project, sample_ids):
    task = CalibrationTask(project, reviewer=None, stage="screening")
    summary = CalibrationSummary(stage="screening", sample_round=1,
                                 sample_size=len(sample_ids), candidates_available=len(sample_ids))
    task._compute_agreement(summary, sample_ids)
    return summary


class TestCalibrationPairing:
    def test_simple_pairing(self, tmp_project):
        db = tmp_project.db
        s1, s2 = _add_source(tmp_project, "A"), _add_source(tmp_project, "B")
        for sid, ai, human in [(s1, "include", "include"), (s2, "exclude", "include")]:
            _vote(db, sid, ai, "gpt", "ai")
            _vote(db, sid, human, "amber", "human")
        summary = _agreement(tmp_project, [s1, s2])
        assert summary.paired_count == 2
        assert summary.agreement == 0.5
        assert summary.human_counts["include"] == 2

    def test_full_text_decisions_do_not_enter_screening_kappa(self, tmp_project):
        """0.24 regression: a full-text stage row must not pair into abstract κ."""
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _vote(db, sid, "include", "gpt", "ai", stage="abstract")
        _vote(db, sid, "include", "amber", "human", stage="abstract")
        _vote(db, sid, "exclude", "amber", "human", stage="full_text")  # must be ignored
        summary = _agreement(tmp_project, [sid])
        assert summary.paired_count == 1
        assert summary.agreement == 1.0

    def test_superseded_revote_uses_latest(self, tmp_project):
        """0.24 regression: the latest re-vote is what pairs, not the first vote."""
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _vote(db, sid, "exclude", "gpt", "ai")
        _vote(db, sid, "include", "amber", "human")
        _vote(db, sid, "exclude", "amber", "human")  # re-vote -> now agrees with AI
        summary = _agreement(tmp_project, [sid])
        assert summary.paired_count == 1
        assert summary.agreement == 1.0
        assert summary.human_counts == {"include": 0, "exclude": 1, "uncertain": 0}

    def test_unpaired_sources_do_not_count(self, tmp_project):
        db = tmp_project.db
        s1 = _add_source(tmp_project, "ai-only")
        s2 = _add_source(tmp_project, "human-only")
        _vote(db, s1, "include", "gpt", "ai")
        _vote(db, s2, "include", "amber", "human")
        summary = _agreement(tmp_project, [s1, s2])
        assert summary.paired_count == 0
        assert math.isnan(summary.kappa)

    def test_empty_sample_is_a_noop(self, tmp_project):
        summary = _agreement(tmp_project, [])
        assert summary.paired_count == 0 and math.isnan(summary.kappa)


class TestQuickTestAgreement:
    """κ for a quick-test run: the AI side is the run's own verdict from the isolated test tables,
    the human side is the reviewer's real decision whenever they made it."""

    def _abstract_run(self, project, verdicts):
        run_id = project.db.create_test_run(
            project_id=project.project_id, stage="abstract", sample_size=len(verdicts),
            prompt_snapshot="p", criteria_snapshot="c",
        )
        for sid, verdict in verdicts.items():
            project.db.insert_test_decision(
                run_id=run_id, source_id=sid, decision=verdict, reasoning="t",
                confidence=8, matched_criteria=[], evidence_quotes=[],
            )
        return run_id

    def _extraction_run(self, project, verdicts):
        run_id = project.db.create_test_run(
            project_id=project.project_id, stage="extraction", sample_size=len(verdicts),
            prompt_snapshot="p", criteria_snapshot="c",
        )
        for sid, verdict in verdicts.items():
            project.db.insert_test_extraction(
                run_id=run_id, source_id=sid, full_text_decision=verdict, fields=[], flag_check=None,
            )
        return run_id

    def test_the_page_shows_the_binary_kappa_with_the_three_way_one_beside_it(self, tmp_project):
        s1, s2 = _add_source(tmp_project, "A"), _add_source(tmp_project, "B")
        run_id = self._abstract_run(tmp_project, {s1: "uncertain", s2: "exclude"})
        _vote(tmp_project.db, s1, "include", "amber", "human")
        _vote(tmp_project.db, s2, "exclude", "amber", "human")

        text = _component_text(calibration_view._agreement_block(tmp_project, run_id, "abstract"))
        assert "κ 1.00" in text             # uncertain vs include agrees once uncertain reads as include
        assert "three-way κ 0.33" in text   # p_o 1/2, p_e 1/4

    def test_pairs_run_verdicts_with_human_decisions(self, tmp_project):
        s1, s2 = _add_source(tmp_project, "A"), _add_source(tmp_project, "B")
        run_id = self._abstract_run(tmp_project, {s1: "include", s2: "exclude"})
        _vote(tmp_project.db, s1, "include", "amber", "human")
        _vote(tmp_project.db, s2, "include", "amber", "human")
        stats = quick_test_agreement(tmp_project, run_id, "abstract")
        assert stats["paired_count"] == 2
        assert stats["agreement"] == 0.5
        assert [d["source_id"] for d in stats["disagreements"]] == [s2]

    def test_real_ai_decisions_do_not_leak_in(self, tmp_project):
        """The reason the test tables exist: a corpus run made under a different prompt must not
        be what this run's κ reports."""
        sid = _add_source(tmp_project)
        run_id = self._abstract_run(tmp_project, {sid: "include"})
        _vote(tmp_project.db, sid, "exclude", "gpt", "ai")  # real run, opposite verdict
        _vote(tmp_project.db, sid, "include", "amber", "human")
        stats = quick_test_agreement(tmp_project, run_id, "abstract")
        assert stats["paired_count"] == 1
        assert stats["agreement"] == 1.0  # paired with the test-table include, not the real exclude

    def test_papers_the_human_has_not_decided_are_unpaired(self, tmp_project):
        s1, s2 = _add_source(tmp_project, "A"), _add_source(tmp_project, "B")
        run_id = self._abstract_run(tmp_project, {s1: "include", s2: "include"})
        _vote(tmp_project.db, s1, "include", "amber", "human")
        stats = quick_test_agreement(tmp_project, run_id, "abstract")
        assert stats["paired_count"] == 1
        assert stats["ai_counts"]["include"] == 2  # both still counted on the AI side

    def test_latest_human_vote_wins(self, tmp_project):
        sid = _add_source(tmp_project)
        run_id = self._abstract_run(tmp_project, {sid: "exclude"})
        _vote(tmp_project.db, sid, "include", "amber", "human")
        _vote(tmp_project.db, sid, "exclude", "amber", "human")  # changed their mind
        assert quick_test_agreement(tmp_project, run_id, "abstract")["agreement"] == 1.0

    def test_extraction_run_pairs_against_the_full_text_stage(self, tmp_project):
        sid = _add_source(tmp_project)
        run_id = self._extraction_run(tmp_project, {sid: "include"})
        _vote(tmp_project.db, sid, "exclude", "amber", "human", stage="abstract")
        assert quick_test_agreement(tmp_project, run_id, "extraction")["paired_count"] == 0
        _vote(tmp_project.db, sid, "include", "amber", "human", stage="full_text")
        stats = quick_test_agreement(tmp_project, run_id, "extraction")
        assert stats["paired_count"] == 1 and stats["agreement"] == 1.0

    def test_extraction_without_a_verdict_is_skipped(self, tmp_project):
        """flag_check off leaves full_text_decision null — nothing to compare."""
        sid = _add_source(tmp_project)
        run_id = self._extraction_run(tmp_project, {sid: None})
        _vote(tmp_project.db, sid, "include", "amber", "human", stage="full_text")
        assert quick_test_agreement(tmp_project, run_id, "extraction")["paired_count"] == 0

    def test_empty_run_reports_no_pairs(self, tmp_project):
        run_id = self._abstract_run(tmp_project, {})
        stats = quick_test_agreement(tmp_project, run_id, "abstract")
        assert stats["paired_count"] == 0 and math.isnan(stats["kappa"])


class TestAgreementStatsCI:
    def test_stats_carry_a_kappa_ci_bracketing_the_estimate(self, tmp_project):
        db = tmp_project.db
        votes = [("include", "include"), ("include", "include"),
                 ("exclude", "exclude"), ("exclude", "include")]
        sids = []
        for i, (ai, human) in enumerate(votes):
            sid = _add_source(tmp_project, f"P{i}")
            _vote(db, sid, ai, "gpt", "ai")
            _vote(db, sid, human, "amber", "human")
            sids.append(sid)
        stats = _agreement_stats(_latest_by_reviewer_type(tmp_project, sids, "abstract"))
        lo, hi = stats["kappa_ci"]
        assert lo <= stats["kappa"] <= hi
        assert lo < hi  # an interval, not a point

    def test_the_interval_narrows_as_the_sample_grows(self, tmp_project):
        """Bracketing the estimate is true of any pair of numbers either side of it. Shrinking with
        n is the property that separates a real interval from a placeholder."""
        db = tmp_project.db
        sids = []
        for i in range(40):
            ai = "include" if i % 2 else "exclude"
            human = ai if i % 4 else ("exclude" if ai == "include" else "include")  # 3 of 4 agree
            sid = _add_source(tmp_project, f"P{i}")
            _vote(db, sid, ai, "gpt", "ai")
            _vote(db, sid, human, "amber", "human")
            sids.append(sid)

        def _width(subset):
            lo, hi = _agreement_stats(_latest_by_reviewer_type(tmp_project, subset, "abstract"))["kappa_ci"]
            return hi - lo

        assert _width(sids) < _width(sids[:8])

    def test_kappa_ci_is_undefined_without_pairs(self, tmp_project):
        sid = _add_source(tmp_project)
        _vote(tmp_project.db, sid, "include", "gpt", "ai")  # no human vote
        stats = _agreement_stats(_latest_by_reviewer_type(tmp_project, [sid], "abstract"))
        assert all(math.isnan(x) for x in stats["kappa_ci"])


class TestPairedScreeningDecisions:
    """The Reports/methods-export pairing must follow the same rule as calibration:
    one pair per source, latest AI vs latest human, stage-scoped."""

    def test_one_pair_per_source_latest_wins(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _vote(db, sid, "exclude", "gpt", "ai")
        _vote(db, sid, "include", "gpt", "ai")      # AI re-run supersedes
        _vote(db, sid, "exclude", "amber", "human")
        _vote(db, sid, "include", "amber", "human")  # re-vote supersedes
        pairs = db.paired_screening_decisions(tmp_project.project_id)
        assert [(p["ai_decision"], p["human_decision"]) for p in pairs] == [("include", "include")]

    def test_full_text_rows_do_not_pair_into_abstract(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _vote(db, sid, "include", "gpt", "ai", stage="abstract")
        _vote(db, sid, "exclude", "amber", "human", stage="full_text")  # no abstract human vote
        assert db.paired_screening_decisions(tmp_project.project_id) == []
        _vote(db, sid, "include", "amber", "human", stage="abstract")
        pairs = db.paired_screening_decisions(tmp_project.project_id)
        assert [(p["ai_decision"], p["human_decision"]) for p in pairs] == [("include", "include")]

    def test_stage_parameter_selects_full_text_pairs(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _vote(db, sid, "exclude", "gpt", "ai", stage="full_text")
        _vote(db, sid, "include", "amber", "human", stage="full_text")
        pairs = db.paired_screening_decisions(tmp_project.project_id, stage="full_text")
        assert [(p["ai_decision"], p["human_decision"]) for p in pairs] == [("exclude", "include")]

    def test_source_without_both_reviewers_is_not_paired(self, tmp_project):
        db = tmp_project.db
        s_ai = _add_source(tmp_project, "ai only")
        s_hum = _add_source(tmp_project, "human only")
        _vote(db, s_ai, "include", "gpt", "ai")
        _vote(db, s_hum, "include", "amber", "human")
        assert db.paired_screening_decisions(tmp_project.project_id) == []

    def test_independent_mode_pairs_the_latest_human(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _vote(db, sid, "include", "gpt", "ai")
        _vote(db, sid, "include", "amber", "human")
        _vote(db, sid, "exclude", "bob", "human")  # latest human row is bob's
        pairs = db.paired_screening_decisions(tmp_project.project_id)
        assert len(pairs) == 1  # one pair, not one per human
        assert pairs[0]["human_decision"] == "exclude" and pairs[0]["human_reviewer_id"] == "bob"
