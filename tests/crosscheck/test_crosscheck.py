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
from tests.helpers import codes, set_config

PAPER = """
# Method

Forty-eight dyads completed the task. Participants were recruited from a univer-
sity subject pool. Mean age was 24.3 years (SD = 3.1).

We used a within-subjects design and recorded gaze with an eye-tracker.
The corpus contained 1,024 utterances in total.
"""


def _row(name, value, quote=None, row_id=1):
    return {"id": row_id, "field_name": name, "value": value, "source_quote": quote}


# ----- Quote matching -----

def test_quote_present_is_not_flagged():
    fields = [FieldSpec(name="design", type="string")]
    rows = [_row("design", "within-subjects", "We used a within-subjects design")]
    assert check_extraction(rows, fields, PAPER) == []


def test_missing_quote_is_flagged():
    fields = [FieldSpec(name="design", type="string")]
    rows = [_row("design", "between-subjects", "We used a between-subjects design")]
    assert codes(check_extraction(rows, fields, PAPER)) == [QUOTE_NOT_FOUND]


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
    assert codes(check_extraction([_row("sample_size", None)], fields, PAPER)) == [EMPTY_REQUIRED]


def test_required_field_never_extracted_is_flagged():
    fields = [FieldSpec(name="sample_size", type="integer", required=True)]
    assert codes(check_extraction([], fields, PAPER)) == [EMPTY_REQUIRED]


def test_optional_field_with_no_value_is_not_flagged():
    fields = [FieldSpec(name="notes", type="string", required=False)]
    assert check_extraction([_row("notes", "")], fields, PAPER) == []


def test_value_outside_the_enum_is_flagged():
    fields = [FieldSpec(name="design", type="string", enum=["within", "between"])]
    rows = [_row("design", "mixed", "We used a within-subjects design")]
    assert codes(check_extraction(rows, fields, PAPER)) == [INVALID_ENUM]


def test_enum_match_is_case_insensitive():
    fields = [FieldSpec(name="design", type="string", enum=["Within", "Between"])]
    rows = [_row("design", "within", "We used a within-subjects design")]
    assert check_extraction(rows, fields, PAPER) == []


# ----- Number vs its own quote -----

def test_number_absent_from_its_quote_is_flagged():
    fields = [FieldSpec(name="sample_size", type="integer")]
    rows = [_row("sample_size", 52, "Forty-eight dyads completed the task")]
    assert codes(check_extraction(rows, fields, PAPER)) == [VALUE_NOT_IN_QUOTE]


def test_number_formatting_difference_is_not_flagged():
    """A whole number stored as a float reads 48.0; the paper says 48."""
    fields = [FieldSpec(name="n_dyads", type="number")]
    rows = [_row("n_dyads", 48.0, "We tested 48 dyads")]
    assert check_extraction(rows, fields, "# Method\n\nWe tested 48 dyads in the lab.") == []


def test_thousands_separator_in_the_quote_is_not_a_mismatch():
    fields = [FieldSpec(name="n_utterances", type="integer")]
    rows = [_row("n_utterances", 1024, "The corpus contained 1,024 utterances")]
    assert check_extraction(rows, fields, PAPER) == []


def test_a_missing_quote_suppresses_the_number_check():
    """One finding per field is enough; a quote that is not in the paper makes the value
    comparison meaningless anyway."""
    fields = [FieldSpec(name="sample_size", type="integer")]
    rows = [_row("sample_size", 52, "Fifty-two dyads took part")]
    assert codes(check_extraction(rows, fields, PAPER)) == [QUOTE_NOT_FOUND]


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

AI_ID = "anthropic:x"


def _seed_extraction(db, project_id, extractor_type="ai", extractor_id=AI_ID, field="design"):
    sid = db.insert_source(Source(title="A paper", project_id=project_id))
    row_id = db.insert_extraction(ExtractionResult(
        extractor_type=extractor_type, extractor_id=extractor_id, field_name=field,
        value="within-subjects", source_quote="We used a within-subjects design", source_id=sid,
    ))
    return sid, row_id


def _finding(sid, row_id, field="design", verdict="disagree", code=QUOTE_NOT_FOUND,
             target_type="ai", target_id=AI_ID):
    return CrossCheckRecord(
        source_id=sid, stage="extraction", target_type=target_type, target_id=target_id,
        target_row_id=row_id, field_name=field, checker_type="ai",
        checker_id="ailr:deterministic", check_kind="deterministic", verdict=verdict,
        issue_code=code, reason="because",
    )


def _store(db, sid, records, target_type="ai", target_id=AI_ID):
    db.replace_cross_checks(sid, "extraction", target_type, target_id, "deterministic", records)


def test_replace_cross_checks_supersedes_the_previous_run(db, tmp_project):
    sid, row_id = _seed_extraction(db, tmp_project.project_id)
    _store(db, sid, [_finding(sid, row_id, code=QUOTE_NOT_FOUND)])
    assert [r["issue_code"] for r in db.get_cross_checks(sid)] == [QUOTE_NOT_FOUND]
    _store(db, sid, [_finding(sid, row_id, code=VALUE_NOT_IN_QUOTE)])
    assert [r["issue_code"] for r in db.get_cross_checks(sid)] == [VALUE_NOT_IN_QUOTE]
    _store(db, sid, [])
    assert db.get_cross_checks(sid) == []


def test_one_extractors_findings_do_not_clear_anothers(db, tmp_project):
    """Two humans can hold rows for the same paper at once; checking one must not wipe the other."""
    sid, amber_row = _seed_extraction(db, tmp_project.project_id, "human", "amber")
    bo_row = db.insert_extraction(ExtractionResult(
        extractor_type="human", extractor_id="bo", field_name="design",
        value="between", source_quote="nope", source_id=sid,
    ))
    _store(db, sid, [_finding(sid, amber_row, target_type="human", target_id="amber")],
           target_type="human", target_id="amber")
    _store(db, sid, [_finding(sid, bo_row, target_type="human", target_id="bo")],
           target_type="human", target_id="bo")

    stored = {r["target_id"] for r in db.get_cross_checks(sid)}
    assert stored == {"amber", "bo"}


def test_cross_checks_by_field_groups_live_findings(db, tmp_project):
    sid, row_id = _seed_extraction(db, tmp_project.project_id)
    _store(db, sid, [_finding(sid, row_id)])
    grouped = db.cross_checks_by_field(sid)
    assert list(grouped) == ["design"]
    assert grouped["design"][0]["issue_code"] == QUOTE_NOT_FOUND


def test_a_finding_goes_stale_when_the_row_it_judged_is_re_extracted(db, tmp_project):
    sid, row_id = _seed_extraction(db, tmp_project.project_id)
    _store(db, sid, [_finding(sid, row_id)])
    assert db.get_cross_checks(sid)[0]["stale"] is False

    db.insert_extraction(ExtractionResult(
        extractor_type="ai", extractor_id=AI_ID, field_name="design",
        value="between-subjects", source_quote="a later run", source_id=sid,
    ))
    assert db.get_cross_checks(sid)[0]["stale"] is True
    assert db.cross_checks_by_field(sid) == {}  # stale findings are not shown as badges


def test_cross_check_counts_ignores_agreeing_rows(db, tmp_project):
    sid, row_id = _seed_extraction(db, tmp_project.project_id)
    _store(db, sid, [
        _finding(sid, row_id, verdict="agree", code=None),
        _finding(sid, row_id, field="sample_size", code=EMPTY_REQUIRED),
    ])
    assert db.cross_check_counts([sid]) == {sid: 1}


# ----- Task -----

def _write_markdown(project, source_id, text=PAPER):
    path = project.root / "data" / "markdown" / f"{source_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_targets_decides_whose_extraction_is_checked(tmp_project):
    """`targets` is the whole point of the setting: with both on, the AI and each human get their
    own stored findings; with only 'ai' on, the human's rows are left alone."""
    from ailr.tasks.crosscheck import DeterministicCrossCheckTask

    db = tmp_project.db
    sid = db.insert_source(Source(title="A paper", project_id=tmp_project.project_id))
    _write_markdown(tmp_project, sid)
    for etype, eid in (("ai", AI_ID), ("human", "amber")):
        db.insert_extraction(ExtractionResult(
            extractor_type=etype, extractor_id=eid, field_name="design",
            value="within-subjects", source_quote="a quote that is not in the paper", source_id=sid,
        ))

    summary = DeterministicCrossCheckTask(tmp_project).run([sid], targets=["ai", "human"])
    assert summary.checked == 2
    assert summary.sources == 1
    assert {r["target_type"] for r in db.get_cross_checks(sid)} == {"ai", "human"}

    db.delete_cross_checks(sid)
    DeterministicCrossCheckTask(tmp_project).run([sid], targets=["ai"])
    assert {r["target_type"] for r in db.get_cross_checks(sid)} == {"ai"}


def test_task_skips_a_paper_with_no_converted_full_text(tmp_project):
    from ailr.tasks.crosscheck import DeterministicCrossCheckTask

    db = tmp_project.db
    sid = db.insert_source(Source(title="No markdown", project_id=tmp_project.project_id))
    db.insert_extraction(ExtractionResult(
        extractor_type="ai", extractor_id=AI_ID, field_name="design",
        value="within", source_quote="q", source_id=sid,
    ))
    summary = DeterministicCrossCheckTask(tmp_project).run([sid], targets=["ai"])
    assert summary.skipped_no_markdown == 1
    assert summary.checked == 0
    assert db.get_cross_checks(sid) == []


def test_cross_check_counts_ignores_stale_findings(db, tmp_project):
    """The queue filter and the badges have to agree on what counts as an open finding."""
    sid, row_id = _seed_extraction(db, tmp_project.project_id)
    _store(db, sid, [_finding(sid, row_id)])
    assert db.cross_check_counts([sid]) == {sid: 1}

    db.insert_extraction(ExtractionResult(
        extractor_type="ai", extractor_id=AI_ID, field_name="design",
        value="between-subjects", source_quote="a later run", source_id=sid,
    ))
    assert db.cross_check_counts([sid]) == {}


# ----- Whose record a finding belongs to -----

def test_a_humans_finding_goes_stale_only_when_that_human_saves_again(db, tmp_project):
    sid, amber_row = _seed_extraction(db, tmp_project.project_id, "human", "amber")
    _store(db, sid, [_finding(sid, amber_row, target_type="human", target_id="amber")],
           target_type="human", target_id="amber")

    db.insert_extraction(ExtractionResult(
        extractor_type="human", extractor_id="bo", field_name="design",
        value="between", source_quote="q", source_id=sid,
    ))
    assert db.get_cross_checks(sid)[0]["stale"] is False   # bo's save is not amber's record

    db.insert_extraction(ExtractionResult(
        extractor_type="human", extractor_id="amber", field_name="design",
        value="between", source_quote="q", source_id=sid,
    ))
    assert db.get_cross_checks(sid)[0]["stale"] is True


def test_an_ai_finding_goes_stale_after_a_re_run_by_any_model(db, tmp_project):
    """There is one live AI extraction per paper, whichever model wrote the newest row."""
    sid, row_id = _seed_extraction(db, tmp_project.project_id)
    _store(db, sid, [_finding(sid, row_id)])
    db.insert_extraction(ExtractionResult(
        extractor_type="ai", extractor_id="openai:y", field_name="design",
        value="between", source_quote="q", source_id=sid,
    ))
    assert db.get_cross_checks(sid)[0]["stale"] is True


def test_without_explicit_targets_the_run_follows_the_setting(tmp_project):
    """The UI starts a run without passing targets. With the setting on humans only, each human
    gets findings of their own and the AI's rows are left alone."""
    from ailr.tasks.crosscheck import DeterministicCrossCheckTask

    project = set_config(tmp_project, "crosscheck", targets=["human"])

    db = project.db
    sid = db.insert_source(Source(title="A paper", project_id=project.project_id))
    _write_markdown(project, sid)
    for etype, eid in (("ai", AI_ID), ("human", "amber"), ("human", "bo")):
        db.insert_extraction(ExtractionResult(
            extractor_type=etype, extractor_id=eid, field_name="primary_research_goal",
            value="synchrony", source_quote="a quote that is not in the paper", source_id=sid,
        ))

    DeterministicCrossCheckTask(project).run([sid])
    assert {(r["target_type"], r["target_id"]) for r in db.get_cross_checks(sid)} == {("human", "amber"), ("human", "bo")}


def test_the_task_judges_the_newest_row_of_a_field(tmp_project):
    """Saving again appends a row; a finding pinned to the older one would be born stale and drop
    out of the badges and counts."""
    from ailr.tasks.crosscheck import DeterministicCrossCheckTask

    db = tmp_project.db
    sid = db.insert_source(Source(title="A paper", project_id=tmp_project.project_id))
    _write_markdown(tmp_project, sid)
    for value in ("first draft", "second draft"):
        newest = db.insert_extraction(ExtractionResult(
            extractor_type="human", extractor_id="amber", field_name="primary_research_goal",
            value=value, source_quote="a quote that is not in the paper", source_id=sid,
        ))

    DeterministicCrossCheckTask(tmp_project).run([sid], targets=["human"])
    [finding] = [r for r in db.get_cross_checks(sid) if r["field_name"] == "primary_research_goal"]
    assert (finding["target_row_id"], finding["stale"]) == (newest, False)
