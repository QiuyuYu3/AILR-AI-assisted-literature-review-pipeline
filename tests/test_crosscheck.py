"""Deterministic cross-checks and their storage."""

from ailr.core.crosscheck import (
    EMPTY_REQUIRED,
    INVALID_ENUM,
    QUOTE_NOT_FOUND,
    VALUE_NOT_IN_QUOTE,
    CrossCheckRecord,
    check_extraction,
    issues_to_records,
)
from ailr.core.source import Source
from ailr.extraction import FieldSpec
from ailr.reviewers import ExtractionResult


PAPER = """
# Method

Forty-eight dyads completed the task. Participants were recruited from a univer-
sity subject pool. Mean age was 24.3 years (SD = 3.1).

We used a within-subjects design and recorded gaze with an eye-tracker.
The corpus contained 1,024 utterances in total.
"""


def _row(name, value, quote=None, row_id=1):
    return {"id": row_id, "field_name": name, "value": value, "source_quote": quote}


def _codes(issues):
    return sorted(i.issue_code for i in issues)


# ----- Quote matching -----

def test_quote_present_is_not_flagged():
    fields = [FieldSpec(name="design", type="string")]
    rows = [_row("design", "within-subjects", "We used a within-subjects design")]
    assert check_extraction(rows, fields, PAPER) == []


def test_missing_quote_is_flagged():
    fields = [FieldSpec(name="design", type="string")]
    rows = [_row("design", "between-subjects", "We used a between-subjects design")]
    assert _codes(check_extraction(rows, fields, PAPER)) == [QUOTE_NOT_FOUND]


def test_quote_across_a_hyphenated_line_break_still_matches():
    """PDF conversion hyphenates at the margin; the model quotes the unbroken word."""
    fields = [FieldSpec(name="recruitment", type="string")]
    rows = [_row("recruitment", "subject pool", "recruited from a university subject pool")]
    assert check_extraction(rows, fields, PAPER) == []


def test_quote_with_elision_matches_both_fragments():
    fields = [FieldSpec(name="design", type="string")]
    rows = [_row("design", "within-subjects", "We used a within-subjects design ... with an eye-tracker")]
    assert check_extraction(rows, fields, PAPER) == []


# ----- Schema checks -----

def test_required_field_with_no_value_is_flagged():
    fields = [FieldSpec(name="sample_size", type="integer", required=True)]
    assert _codes(check_extraction([_row("sample_size", None)], fields, PAPER)) == [EMPTY_REQUIRED]


def test_required_field_never_extracted_is_flagged():
    fields = [FieldSpec(name="sample_size", type="integer", required=True)]
    assert _codes(check_extraction([], fields, PAPER)) == [EMPTY_REQUIRED]


def test_optional_field_with_no_value_is_not_flagged():
    fields = [FieldSpec(name="notes", type="string", required=False)]
    assert check_extraction([_row("notes", "")], fields, PAPER) == []


def test_value_outside_the_enum_is_flagged():
    fields = [FieldSpec(name="design", type="string", enum=["within", "between"])]
    rows = [_row("design", "mixed", "We used a within-subjects design")]
    assert _codes(check_extraction(rows, fields, PAPER)) == [INVALID_ENUM]


def test_enum_match_is_case_insensitive():
    fields = [FieldSpec(name="design", type="string", enum=["Within", "Between"])]
    rows = [_row("design", "within", "We used a within-subjects design")]
    assert check_extraction(rows, fields, PAPER) == []


# ----- Number vs its own quote -----

def test_number_absent_from_its_quote_is_flagged():
    fields = [FieldSpec(name="sample_size", type="integer")]
    rows = [_row("sample_size", 52, "Forty-eight dyads completed the task")]
    assert _codes(check_extraction(rows, fields, PAPER)) == [VALUE_NOT_IN_QUOTE]


def test_number_formatting_difference_is_not_flagged():
    fields = [FieldSpec(name="mean_age", type="number")]
    rows = [_row("mean_age", 24.3, "Mean age was 24.3 years")]
    assert check_extraction(rows, fields, PAPER) == []


def test_thousands_separator_in_the_quote_is_not_a_mismatch():
    fields = [FieldSpec(name="n_utterances", type="integer")]
    rows = [_row("n_utterances", 1024, "The corpus contained 1,024 utterances")]
    assert check_extraction(rows, fields, PAPER) == []


def test_a_missing_quote_suppresses_the_number_check():
    """One finding per field is enough; a quote that is not in the paper makes the value
    comparison meaningless anyway."""
    fields = [FieldSpec(name="sample_size", type="integer")]
    rows = [_row("sample_size", 52, "Fifty-two dyads took part")]
    assert _codes(check_extraction(rows, fields, PAPER)) == [QUOTE_NOT_FOUND]


def test_only_the_latest_row_per_field_is_checked():
    """A re-run appends rows; the superseded one must not raise a finding of its own."""
    fields = [FieldSpec(name="design", type="string")]
    rows = [
        _row("design", "between-subjects", "We used a between-subjects design", row_id=1),
        _row("design", "within-subjects", "We used a within-subjects design", row_id=2),
    ]
    assert check_extraction(rows, fields, PAPER) == []


# ----- Records -----

def test_clean_fields_get_an_explicit_agree_row():
    fields = [FieldSpec(name="design", type="string"), FieldSpec(name="notes", type="string", required=False)]
    rows = [
        _row("design", "mixed", "not in the paper at all", row_id=7),
        _row("notes", "n/a", "recorded gaze with an eye-tracker", row_id=8),
    ]
    issues = check_extraction(rows, fields, PAPER)
    records = issues_to_records(
        issues, source_id=1, target_type="ai", target_id="anthropic:x",
        row_ids={"design": 7, "notes": 8}, checked_fields=["design", "notes"],
    )
    by_field = {r.field_name: r for r in records}
    assert by_field["design"].verdict == "disagree"
    assert by_field["design"].target_row_id == 7
    assert by_field["notes"].verdict == "agree"
    assert by_field["notes"].issue_code is None


# ----- Storage -----

def _seed_extraction(db, project_id, quote="We used a within-subjects design"):
    sid = db.insert_source(Source(title="A paper", project_id=project_id))
    row_id = db.insert_extraction(ExtractionResult(
        extractor_type="ai", extractor_id="anthropic:x", field_name="design",
        value="within-subjects", source_quote=quote, source_id=sid,
    ))
    return sid, row_id


def test_replace_cross_checks_supersedes_the_previous_run(db, tmp_project):
    sid, row_id = _seed_extraction(db, tmp_project.project_id)
    first = [CrossCheckRecord(
        source_id=sid, stage="extraction", target_type="ai", target_id="anthropic:x",
        target_row_id=row_id, field_name="design", checker_type="ai",
        checker_id="ailr:deterministic", check_kind="deterministic", verdict="disagree",
        issue_code=QUOTE_NOT_FOUND, reason="nope",
    )]
    db.replace_cross_checks(sid, "extraction", "ai", "deterministic", first)
    db.replace_cross_checks(sid, "extraction", "ai", "deterministic", [])
    assert db.get_cross_checks(sid) == []


def test_cross_checks_by_field_groups_live_findings(db, tmp_project):
    sid, row_id = _seed_extraction(db, tmp_project.project_id)
    db.replace_cross_checks(sid, "extraction", "ai", "deterministic", [CrossCheckRecord(
        source_id=sid, stage="extraction", target_type="ai", target_id="anthropic:x",
        target_row_id=row_id, field_name="design", checker_type="ai",
        checker_id="ailr:deterministic", check_kind="deterministic", verdict="disagree",
        issue_code=QUOTE_NOT_FOUND, reason="not found",
    )])
    grouped = db.cross_checks_by_field(sid)
    assert list(grouped) == ["design"]
    assert grouped["design"][0]["issue_code"] == QUOTE_NOT_FOUND


def test_a_finding_goes_stale_when_the_row_it_judged_is_re_extracted(db, tmp_project):
    sid, row_id = _seed_extraction(db, tmp_project.project_id)
    db.replace_cross_checks(sid, "extraction", "ai", "deterministic", [CrossCheckRecord(
        source_id=sid, stage="extraction", target_type="ai", target_id="anthropic:x",
        target_row_id=row_id, field_name="design", checker_type="ai",
        checker_id="ailr:deterministic", check_kind="deterministic", verdict="disagree",
        issue_code=QUOTE_NOT_FOUND, reason="not found",
    )])
    assert db.get_cross_checks(sid)[0]["stale"] is False

    db.insert_extraction(ExtractionResult(
        extractor_type="ai", extractor_id="anthropic:x", field_name="design",
        value="between-subjects", source_quote="a later run", source_id=sid,
    ))
    assert db.get_cross_checks(sid)[0]["stale"] is True
    assert db.cross_checks_by_field(sid) == {}  # stale findings are not shown as badges


def test_cross_check_counts_ignores_agreeing_rows(db, tmp_project):
    sid, row_id = _seed_extraction(db, tmp_project.project_id)
    db.replace_cross_checks(sid, "extraction", "ai", "deterministic", [
        CrossCheckRecord(
            source_id=sid, stage="extraction", target_type="ai", target_id="anthropic:x",
            target_row_id=row_id, field_name="design", checker_type="ai",
            checker_id="ailr:deterministic", check_kind="deterministic", verdict="agree",
        ),
        CrossCheckRecord(
            source_id=sid, stage="extraction", target_type="ai", target_id="anthropic:x",
            target_row_id=row_id, field_name="sample_size", checker_type="ai",
            checker_id="ailr:deterministic", check_kind="deterministic", verdict="disagree",
            issue_code=EMPTY_REQUIRED, reason="empty",
        ),
    ])
    assert db.cross_check_counts([sid]) == {sid: 1}
