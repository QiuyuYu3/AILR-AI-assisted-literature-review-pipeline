"""Agreement metrics and the calibration pairing.

The pairing regression (0.24): κ must compare only the LATEST decision per reviewer type
at the ABSTRACT stage — full-text decisions and superseded re-votes used to skew it.
"""

import math

from ailr.core.source import Source
from ailr.metrics import (
    BINARY_CATEGORIES,
    binarize,
    cohen_kappa,
    confusion_matrix,
    pabak,
    percent_agreement,
)
from ailr.reviewers import ScreeningDecision
from ailr.tasks.calibrate import CalibrationSummary, CalibrationTask


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
        pairs = [("include", "include"), ("weird", "include")]
        cats = ["include", "exclude", "uncertain"]
        assert cohen_kappa(pairs, categories=cats) == cohen_kappa([("include", "include")], categories=cats)

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
