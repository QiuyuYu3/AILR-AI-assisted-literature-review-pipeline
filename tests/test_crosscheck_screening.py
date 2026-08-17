"""Deterministic cross-checks over a screening decision."""

from ailr.core.crosscheck import EMPTY_REQUIRED, INVALID_ENUM, QUOTE_NOT_FOUND
from ailr.core.crosscheck_screening import (
    DECISION_FLAG_MISMATCH,
    UNKNOWN_CRITERION,
    check_screening_decision,
    checked_fields,
    screening_text,
)


ABSTRACT = (
    "Thirty-two dyads completed a joint attention task while gaze was recorded with a mobile "
    "eye-tracker. Participants were undergraduates recruited from a subject pool. We report "
    "inter-brain synchrony during the interaction."
)

CRITERIA = ["C1", "C2", "C3"]


class _Src:
    def __init__(self, title="Dyadic gaze during joint attention", abstract=ABSTRACT):
        self.title = title
        self.abstract = abstract


def _decision(**over):
    base = {
        "id": 7,
        "decision": "include",
        "reasoning": "Dyadic interaction with gaze recording.",
        "evidence_quotes": ["Thirty-two dyads completed a joint attention task"],
        "matched_criteria": ["C1"],
        "flag_check": [],
    }
    base.update(over)
    return base


def _codes(issues):
    return sorted(i.issue_code for i in issues)


def _flags(*pairs):
    return [{"criterion_id": cid, "verdict": v, "reason": "r"} for cid, v in pairs]


# ----- Quotes -----

def test_clean_decision_is_not_flagged():
    assert check_screening_decision(_decision(), CRITERIA, ABSTRACT) == []


def test_quote_absent_from_the_abstract_is_flagged():
    d = _decision(evidence_quotes=["participants completed an fMRI scan"])
    assert _codes(check_screening_decision(d, CRITERIA, ABSTRACT)) == [QUOTE_NOT_FOUND]


def test_quote_matching_the_title_counts_as_evidence():
    src = _Src()
    d = _decision(evidence_quotes=["Dyadic gaze during joint attention"])
    assert check_screening_decision(d, CRITERIA, screening_text(src)) == []


def test_missing_quotes_are_flagged_for_ai_but_not_for_humans():
    d = _decision(evidence_quotes=[])
    assert _codes(check_screening_decision(d, CRITERIA, ABSTRACT)) == [EMPTY_REQUIRED]
    assert check_screening_decision(d, CRITERIA, ABSTRACT, require_evidence=False) == []


# ----- The record's own vocabulary -----

def test_unknown_decision_value_is_flagged():
    d = _decision(decision="maybe")
    assert INVALID_ENUM in _codes(check_screening_decision(d, CRITERIA, ABSTRACT))


def test_missing_reason_is_flagged():
    d = _decision(reasoning="   ")
    assert EMPTY_REQUIRED in _codes(check_screening_decision(d, CRITERIA, ABSTRACT))


def test_criterion_id_that_does_not_exist_is_flagged():
    d = _decision(matched_criteria=["C1", "C9"])
    assert _codes(check_screening_decision(d, CRITERIA, ABSTRACT)) == [UNKNOWN_CRITERION]


def test_exclude_without_citing_a_criterion_is_flagged():
    d = _decision(decision="exclude", matched_criteria=[])
    assert EMPTY_REQUIRED in _codes(check_screening_decision(d, CRITERIA, ABSTRACT))


# ----- flag_check -----

def test_flag_check_covering_every_criterion_is_clean():
    d = _decision(flag_check=_flags(("C1", "PASS"), ("C2", "PASS"), ("C3", "PASS")))
    assert check_screening_decision(d, CRITERIA, ABSTRACT) == []


def test_criterion_without_a_verdict_is_flagged():
    d = _decision(flag_check=_flags(("C1", "PASS"), ("C2", "PASS")))
    assert _codes(check_screening_decision(d, CRITERIA, ABSTRACT)) == [EMPTY_REQUIRED]


def test_invalid_flag_verdict_is_flagged():
    d = _decision(flag_check=_flags(("C1", "MAYBE"), ("C2", "PASS"), ("C3", "PASS")))
    assert INVALID_ENUM in _codes(check_screening_decision(d, CRITERIA, ABSTRACT))


def test_flag_check_quote_absent_from_the_abstract_is_flagged():
    fc = _flags(("C1", "PASS"), ("C2", "PASS"), ("C3", "PASS"))
    fc[0]["quote"] = "participants completed an fMRI scan"
    d = _decision(flag_check=fc)
    assert _codes(check_screening_decision(d, CRITERIA, ABSTRACT)) == [QUOTE_NOT_FOUND]


# ----- Decision against its own verdicts -----

def test_include_despite_a_failed_criterion_is_flagged():
    d = _decision(flag_check=_flags(("C1", "PASS"), ("C2", "FAIL"), ("C3", "PASS")))
    assert DECISION_FLAG_MISMATCH in _codes(check_screening_decision(d, CRITERIA, ABSTRACT))


def test_exclude_with_everything_passing_is_flagged():
    d = _decision(decision="exclude", flag_check=_flags(("C1", "PASS"), ("C2", "PASS"), ("C3", "PASS")))
    assert DECISION_FLAG_MISMATCH in _codes(check_screening_decision(d, CRITERIA, ABSTRACT))


def test_uncertain_verdicts_do_not_contradict_either_decision():
    """At the abstract stage leniency is the instruction, so UNCERTAIN legitimately reads either way."""
    flags = _flags(("C1", "PASS"), ("C2", "UNCERTAIN"), ("C3", "PASS"))
    for decision in ("include", "exclude", "uncertain"):
        d = _decision(decision=decision, flag_check=flags)
        assert DECISION_FLAG_MISMATCH not in _codes(check_screening_decision(d, CRITERIA, ABSTRACT))


# ----- What counts as checked -----

def test_criteria_join_the_checked_fields_only_when_flag_check_exists():
    assert "C1" not in checked_fields(CRITERIA, [])
    assert "C1" in checked_fields(CRITERIA, _flags(("C1", "PASS")))
