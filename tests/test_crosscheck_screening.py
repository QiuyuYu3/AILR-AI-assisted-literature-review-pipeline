"""Cross-checks over a screening decision: the checks, their storage, and the task walk."""

import json
from pathlib import Path

import pytest

from ailr.core.crosscheck import EMPTY_REQUIRED, INVALID_ENUM, QUOTE_NOT_FOUND
from ailr.core.crosscheck_screening import (
    DECISION_FLAG_MISMATCH,
    UNKNOWN_CRITERION,
    check_screening_decision,
    checked_fields,
    screening_text,
)
from ailr.core.source import Source
from ailr.criteria import save_criteria
from ailr.crosschecker import BUILT_IN_SCREENING_PROMPT, ScreeningCrossChecker, load_prompt
from ailr.exceptions import LLMError
from ailr.reviewers import ScreeningDecision
from tests.helpers import StubClient, add_source, codes

ABSTRACT = (
    "Thirty-two dyads completed a joint attention task while gaze was recorded with a mobile "
    "eye-tracker. Participants were undergraduates recruited from a subject pool. We report "
    "inter-brain synchrony during the interaction."
)

CRITERIA = ["C1", "C2", "C3"]


class _Src:
    def __init__(self, title="Dyadic gaze during joint attention", abstract=ABSTRACT, year=2021):
        self.title = title
        self.abstract = abstract
        self.year = year


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


def _flags(*pairs):
    return [{"criterion_id": cid, "verdict": v, "reason": "r"} for cid, v in pairs]


# ----- Quotes -----

def test_clean_decision_is_not_flagged():
    assert check_screening_decision(_decision(), CRITERIA, ABSTRACT) == []


def test_quote_absent_from_the_abstract_is_flagged():
    d = _decision(evidence_quotes=["participants completed an fMRI scan"])
    assert codes(check_screening_decision(d, CRITERIA, ABSTRACT)) == [QUOTE_NOT_FOUND]


def test_quote_matching_the_title_counts_as_evidence():
    src = _Src()
    d = _decision(evidence_quotes=["Dyadic gaze during joint attention"])
    assert check_screening_decision(d, CRITERIA, screening_text(src)) == []


def test_missing_quotes_are_flagged_for_ai_but_not_for_humans():
    d = _decision(evidence_quotes=[])
    assert codes(check_screening_decision(d, CRITERIA, ABSTRACT)) == [EMPTY_REQUIRED]
    assert check_screening_decision(d, CRITERIA, ABSTRACT, require_evidence=False) == []


# ----- The record's own vocabulary -----

def test_unknown_decision_value_is_flagged():
    d = _decision(decision="maybe")
    assert INVALID_ENUM in codes(check_screening_decision(d, CRITERIA, ABSTRACT))


def test_missing_reason_is_flagged():
    d = _decision(reasoning="   ")
    assert EMPTY_REQUIRED in codes(check_screening_decision(d, CRITERIA, ABSTRACT))


def test_criterion_id_that_does_not_exist_is_flagged():
    d = _decision(matched_criteria=["C1", "C9"])
    assert codes(check_screening_decision(d, CRITERIA, ABSTRACT)) == [UNKNOWN_CRITERION]


def test_exclude_without_citing_a_criterion_is_flagged():
    d = _decision(decision="exclude", matched_criteria=[])
    assert codes(check_screening_decision(d, CRITERIA, ABSTRACT)) == [EMPTY_REQUIRED]


# ----- flag_check -----

def test_flag_check_covering_every_criterion_is_clean():
    d = _decision(flag_check=_flags(("C1", "PASS"), ("C2", "PASS"), ("C3", "PASS")))
    assert check_screening_decision(d, CRITERIA, ABSTRACT) == []


def test_criterion_without_a_verdict_is_flagged():
    d = _decision(flag_check=_flags(("C1", "PASS"), ("C2", "PASS")))
    assert codes(check_screening_decision(d, CRITERIA, ABSTRACT)) == [EMPTY_REQUIRED]


def test_invalid_flag_verdict_is_flagged():
    d = _decision(flag_check=_flags(("C1", "MAYBE"), ("C2", "PASS"), ("C3", "PASS")))
    assert codes(check_screening_decision(d, CRITERIA, ABSTRACT)) == [INVALID_ENUM]


def test_flag_check_quote_absent_from_the_abstract_is_flagged():
    fc = _flags(("C1", "PASS"), ("C2", "PASS"), ("C3", "PASS"))
    fc[0]["quote"] = "participants completed an fMRI scan"
    d = _decision(flag_check=fc)
    assert codes(check_screening_decision(d, CRITERIA, ABSTRACT)) == [QUOTE_NOT_FOUND]


# ----- Decision against its own verdicts -----

def test_include_despite_a_failed_criterion_is_flagged():
    d = _decision(flag_check=_flags(("C1", "PASS"), ("C2", "FAIL"), ("C3", "PASS")))
    assert codes(check_screening_decision(d, CRITERIA, ABSTRACT)) == [DECISION_FLAG_MISMATCH]


def test_exclude_with_everything_passing_is_flagged():
    d = _decision(decision="exclude", flag_check=_flags(("C1", "PASS"), ("C2", "PASS"), ("C3", "PASS")))
    assert codes(check_screening_decision(d, CRITERIA, ABSTRACT)) == [DECISION_FLAG_MISMATCH]


def test_a_well_supported_exclude_is_clean():
    """Cites the criterion it fails and quotes the abstract, with or without per-criterion verdicts
    (which it agrees with when present): nothing to flag."""
    with_verdicts = _decision(decision="exclude", matched_criteria=["C2"],
                              flag_check=_flags(("C1", "PASS"), ("C2", "FAIL"), ("C3", "PASS")))
    citing_only = _decision(decision="exclude", matched_criteria=["C2"])
    for d in (with_verdicts, citing_only):
        assert check_screening_decision(d, CRITERIA, ABSTRACT) == []


def test_uncertain_verdicts_do_not_contradict_either_decision():
    """At the abstract stage leniency is the instruction, so UNCERTAIN legitimately reads either way."""
    flags = _flags(("C1", "PASS"), ("C2", "UNCERTAIN"), ("C3", "PASS"))
    for decision in ("include", "exclude", "uncertain"):
        d = _decision(decision=decision, flag_check=flags)
        assert DECISION_FLAG_MISMATCH not in codes(check_screening_decision(d, CRITERIA, ABSTRACT))


# ----- What counts as checked -----

def test_criteria_join_the_checked_fields_only_when_flag_check_exists():
    assert "C1" not in checked_fields(CRITERIA, [])
    assert "C1" in checked_fields(CRITERIA, _flags(("C1", "PASS")))


# ----- Reading the record back out of the database -----

AI_ID = "anthropic:x"


def _seed_criteria(project, ids=("C1", "C2")):
    save_criteria(
        project.root / project.config.screening.criteria_structured,
        [{"id": cid, "name": f"Criterion {cid}", "pass_if": "yes", "fail_if": "no"} for cid in ids],
    )


def _seed_source(project, title="Dyadic gaze", abstract=ABSTRACT):
    return add_source(project, title, abstract=abstract)


def _decide(project, sid, decision="include", quotes=None, flag_check=None,
            reviewer_type="ai", reviewer_id=AI_ID, stage="abstract"):
    raw = json.dumps({"_flag_check": flag_check}) if flag_check else None
    return project.db.insert_screening_decision(ScreeningDecision(
        decision=decision, reasoning="because", reviewer_type=reviewer_type, reviewer_id=reviewer_id,
        source_id=sid, stage=stage,
        evidence_quotes=quotes if quotes is not None else ["Thirty-two dyads"],
        matched_criteria=["C1"], raw_output=raw,
    ))


def test_only_the_newest_decision_per_reviewer_is_read_back(tmp_project):
    """Re-screening appends, so the cross-check must judge the live record and not a superseded one."""
    sid = _seed_source(tmp_project)
    _decide(tmp_project, sid, decision="exclude")
    latest = _decide(tmp_project, sid, decision="include")
    _decide(tmp_project, sid, reviewer_type="human", reviewer_id="amber")

    rows = tmp_project.db.latest_screening_decisions(sid, "abstract")
    assert len(rows) == 2
    ai_row = next(r for r in rows if r["reviewer_type"] == "ai")
    assert (ai_row["id"], ai_row["decision"]) == (latest, "include")


def test_flag_check_is_decoded_out_of_raw_output(tmp_project):
    sid = _seed_source(tmp_project)
    _decide(tmp_project, sid, flag_check={"C1": {"verdict": "PASS", "reason": "r"}})
    row = tmp_project.db.latest_screening_decisions(sid, "abstract")[0]
    assert row["flag_check"] == [{"criterion_id": "C1", "verdict": "PASS", "reason": "r"}]


def test_reviewer_types_filter_what_is_read_back(tmp_project):
    sid = _seed_source(tmp_project)
    _decide(tmp_project, sid)
    _decide(tmp_project, sid, reviewer_type="human", reviewer_id="amber")
    rows = tmp_project.db.latest_screening_decisions(sid, "abstract", ["ai"])
    assert [r["reviewer_type"] for r in rows] == ["ai"]


def test_candidate_sources_are_the_ones_carrying_a_decision(tmp_project):
    decided = _seed_source(tmp_project)
    _seed_source(tmp_project, title="Never screened")
    _decide(tmp_project, decided)
    assert tmp_project.db.source_ids_with_decisions(tmp_project.project_id, "abstract") == [decided]


# ----- The task -----

def _run(project, source_ids=None, targets=("ai",)):
    from ailr.tasks.crosscheck import ScreeningCrossCheckTask

    ids = source_ids if source_ids is not None else project.db.source_ids_with_decisions(
        project.project_id, "abstract"
    )
    return ScreeningCrossCheckTask(project).run(ids, targets=list(targets))


def test_task_stores_a_finding_for_a_quote_that_is_not_in_the_abstract(tmp_project):
    _seed_criteria(tmp_project)
    sid = _seed_source(tmp_project)
    _decide(tmp_project, sid, quotes=["participants completed an fMRI scan"])

    summary = _run(tmp_project)
    assert (summary.checked, summary.sources, summary.findings) == (1, 1, 1)
    assert summary.per_issue == {QUOTE_NOT_FOUND: 1}

    stored = tmp_project.db.get_cross_checks(sid, stage="abstract")
    flagged = [r for r in stored if r["verdict"] == "disagree"]
    assert [r["field_name"] for r in flagged] == ["evidence_quotes"]
    # Clean parts still get an explicit agree row, so "checked and fine" is distinguishable
    # from "never checked" — same contract as the extraction layer.
    assert {r["field_name"] for r in stored if r["verdict"] == "agree"} >= {"decision", "reasoning"}


def test_task_skips_a_source_with_no_abstract(tmp_project):
    sid = tmp_project.db.insert_source(Source(title="", abstract="", project_id=tmp_project.project_id))
    _decide(tmp_project, sid)
    summary = _run(tmp_project, [sid])
    assert (summary.skipped_no_markdown, summary.checked) == (1, 0)
    assert tmp_project.db.get_cross_checks(sid) == []


def test_targets_decides_whose_decision_is_checked(tmp_project):
    _seed_criteria(tmp_project)
    sid = _seed_source(tmp_project)
    _decide(tmp_project, sid)
    _decide(tmp_project, sid, reviewer_type="human", reviewer_id="amber")

    _run(tmp_project, [sid], targets=("ai", "human"))
    assert {r["target_type"] for r in tmp_project.db.get_cross_checks(sid)} == {"ai", "human"}

    tmp_project.db.delete_cross_checks(sid)
    _run(tmp_project, [sid], targets=("ai",))
    assert {r["target_type"] for r in tmp_project.db.get_cross_checks(sid)} == {"ai"}


def test_a_human_decision_is_not_flagged_for_having_no_quotes(tmp_project):
    """Humans record a verdict and a reason, not quotes; flagging that would flag every one of them."""
    sid = _seed_source(tmp_project)
    _decide(tmp_project, sid, quotes=[], reviewer_type="human", reviewer_id="amber")
    summary = _run(tmp_project, [sid], targets=("human",))
    assert (summary.checked, summary.findings) == (1, 0)


def test_mock_decisions_are_not_flagged_for_having_no_quotes(tmp_project):
    sid = _seed_source(tmp_project)
    _decide(tmp_project, sid, quotes=[], reviewer_id="mock:mock-screen")
    summary = _run(tmp_project, [sid])
    assert summary.findings == 0


# ----- Staleness and the queue filter -----

def test_a_finding_goes_stale_when_the_decision_it_judged_is_re_screened(tmp_project):
    """The screening stage resolves staleness against screening_decisions, not extractions."""
    db = tmp_project.db
    sid = _seed_source(tmp_project)
    _decide(tmp_project, sid, quotes=["participants completed an fMRI scan"])
    _run(tmp_project, [sid])
    assert db.get_cross_checks(sid, stage="abstract")[0]["stale"] is False
    assert db.cross_check_counts([sid], stage="abstract") == {sid: 1}

    _decide(tmp_project, sid)  # re-screened: the record the findings judged is gone
    assert all(r["stale"] for r in db.get_cross_checks(sid, stage="abstract"))
    assert db.cross_check_counts([sid], stage="abstract") == {}


def test_another_reviewers_vote_leaves_a_finding_live(tmp_project):
    """Only the reviewer whose decision was judged can retire a finding by re-screening: a human
    screening the paper after the AI must leave the AI's finding live, counted and listed."""
    db = tmp_project.db
    sid = _seed_source(tmp_project)
    _decide(tmp_project, sid, quotes=["participants completed an fMRI scan"])
    _run(tmp_project, [sid])
    _decide(tmp_project, sid, reviewer_type="human", reviewer_id="amber")
    _decide(tmp_project, sid, reviewer_id="openai:another-model")   # same type, another reviewer

    assert db.get_cross_checks(sid, stage="abstract")[0]["stale"] is False
    assert db.cross_check_counts([sid], stage="abstract") == {sid: 1}
    rows, _, _ = db.list_sources_page(tmp_project.project_id, "amber", stage="abstract", status="crosscheck_flagged")
    assert [s.id for s in rows] == [sid]


def test_the_queue_filter_lists_flagged_papers_and_drops_them_once_re_screened(tmp_project):
    db = tmp_project.db
    flagged = _seed_source(tmp_project)
    clean = _seed_source(tmp_project, title="Nothing wrong here")
    _decide(tmp_project, flagged, quotes=["participants completed an fMRI scan"])
    _decide(tmp_project, clean)
    _run(tmp_project)

    def _listed():
        rows, _, _ = db.list_sources_page(
            tmp_project.project_id, "amber", stage="abstract", status="crosscheck_flagged"
        )
        return [s.id for s in rows]

    assert _listed() == [flagged]
    _decide(tmp_project, flagged)
    assert _listed() == []


def test_screening_and_extraction_findings_do_not_leak_into_each_other(tmp_project):
    """Both stages share the cross_checks table; only `stage` keeps them apart."""
    from ailr.core.crosscheck import CrossCheckRecord

    db = tmp_project.db
    sid = _seed_source(tmp_project)
    _decide(tmp_project, sid, quotes=["participants completed an fMRI scan"])
    _run(tmp_project, [sid])
    db.replace_cross_checks(sid, "extraction", "ai", AI_ID, "deterministic", [
        CrossCheckRecord(
            source_id=sid, stage="extraction", target_type="ai", target_id=AI_ID,
            target_row_id=1, field_name="design", checker_type="ai",
            checker_id="ailr:deterministic", check_kind="deterministic", verdict="disagree",
            issue_code=QUOTE_NOT_FOUND, reason="because",
        )
    ])

    assert db.cross_check_counts([sid], stage="abstract") == {sid: 1}
    assert db.cross_check_counts([sid]) == {sid: 1}
    # The extraction badges read by field name; the screening pseudo-fields must not appear there.
    assert list(db.cross_checks_by_field(sid)) == ["design"]


# ----- The LLM layer -----


def _checked(output=None, decision=None, **kwargs):
    client = StubClient(output or {"verdict": "agree", "reason": "fine", "suggested_value": None, "confidence": 8})
    prompt = load_prompt(Path("no-such-project"), "prompts/absent.txt", BUILT_IN_SCREENING_PROMPT)
    verdicts = ScreeningCrossChecker(client).check(
        _Src(), decision or _decision(), prompt, **kwargs
    )
    return client, verdicts


def test_the_decision_gets_one_verdict_not_one_per_criterion():
    _, verdicts = _checked()
    assert list(verdicts) == ["decision"]
    assert verdicts["decision"]["verdict"] == "agree"


def test_an_unknown_verdict_is_rejected():
    with pytest.raises(LLMError):
        _checked({"verdict": "probably", "reason": "", "suggested_value": None, "confidence": 5})


def test_the_checker_is_not_shown_the_screeners_confidence():
    """Same rule as the extraction checker: the screener's own confidence anchors the judgement."""
    client, _ = _checked(decision=_decision(confidence=9))
    assert "confidence" not in client.calls[0]["user"].lower()


def test_the_message_carries_the_decision_its_quotes_and_the_abstract():
    client, _ = _checked()
    message = client.calls[0]["user"]
    assert "decision: include" in message
    assert "Thirty-two dyads completed a joint attention task" in message
    assert ABSTRACT in message


def test_criteria_and_additional_instructions_reach_the_prompt():
    client, _ = _checked(criteria="C1: dyadic interaction", additional="be lenient at this stage")
    system = client.calls[0]["system"]
    assert "C1: dyadic interaction" in system
    assert "be lenient at this stage" in system


def test_the_project_can_override_the_built_in_screening_prompt(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "crosscheck_screening.txt").write_text("my own prompt", encoding="utf-8")
    rel = "prompts/crosscheck_screening.txt"
    assert load_prompt(tmp_path, rel, BUILT_IN_SCREENING_PROMPT) == "my own prompt"
    assert "abstract" in load_prompt(tmp_path, "prompts/absent.txt", BUILT_IN_SCREENING_PROMPT).lower()


def test_the_llm_task_stores_a_verdict_against_the_decision_it_judged(tmp_project):
    """The paid screening layer end to end: one call per decision, stored as an llm row pinned to
    that decision, with the project's criteria and additional instructions in the prompt."""
    from ailr.tasks.crosscheck import ScreeningLLMCrossCheckTask

    _seed_criteria(tmp_project)
    additional = tmp_project.root / tmp_project.config.crosscheck.screening_additional
    additional.parent.mkdir(parents=True, exist_ok=True)
    additional.write_text("be lenient at this stage", encoding="utf-8")
    sid = _seed_source(tmp_project)
    decision_id = _decide(tmp_project, sid)
    client = StubClient({"verdict": "disagree", "reason": "the abstract never mentions gaze",
                          "suggested_value": None, "confidence": 7})

    summary = ScreeningLLMCrossCheckTask(tmp_project, ScreeningCrossChecker(client)).run([sid], targets=["ai"])
    assert (summary.checked, summary.findings) == (1, 1)
    [row] = tmp_project.db.get_cross_checks(sid, stage="abstract")
    assert (row["check_kind"], row["verdict"], row["target_row_id"]) == ("llm", "disagree", decision_id)
    assert row["reason"] == "the abstract never mentions gaze"
    system = client.calls[0]["system"]
    assert "be lenient at this stage" in system and "Criterion C1" in system
