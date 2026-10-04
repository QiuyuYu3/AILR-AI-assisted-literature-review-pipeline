"""The LLM cross-check layer: prompt resolution, the call, and how verdicts are stored."""

import pytest

from ailr.core.config import Config, crosscheck_llm_blocked
from ailr.core.crosscheck import CrossCheckRecord, llm_verdicts_to_records
from ailr.core.source import Source
from ailr.crosschecker import LLMCrossChecker, build_tool_schema, format_record_message, load_prompt
from ailr.exceptions import LLMError
from ailr.extraction import FieldSpec
from ailr.reviewers import ExtractionResult
from tests.helpers import StubClient

FIELDS = [FieldSpec(name="design", type="string"), FieldSpec(name="sample_size", type="integer")]


def _rows():
    return [
        {"id": 1, "field_name": "design", "value": "within-subjects", "source_quote": "a within-subjects design"},
        {"id": 2, "field_name": "sample_size", "value": 48, "source_quote": "Forty-eight dyads"},
    ]


def _verdict(v="agree", reason="fine", suggested=None, conf=8):
    return {"verdict": v, "reason": reason, "suggested_value": suggested, "confidence": conf}


# ----- Tool schema -----

def test_every_field_gets_a_required_slot():
    schema = build_tool_schema(["design", "sample_size"]).input_schema
    assert set(schema["properties"]) == {"design", "sample_size"}
    assert set(schema["required"]) == {"design", "sample_size"}
    assert schema["properties"]["design"]["properties"]["verdict"]["enum"] == ["agree", "disagree", "uncertain"]


# ----- Prompt -----

def test_project_prompt_overrides_the_built_in_one(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "crosscheck.txt").write_text("my own prompt", encoding="utf-8")
    assert load_prompt(tmp_path) == "my own prompt"


def test_built_in_prompt_is_used_when_the_project_has_none(tmp_path):
    text = load_prompt(tmp_path)
    assert "{{schema_md}}" in text
    assert "{{additional}}" in text
    assert "{{project_name}}" in text
    assert "verifying a structured data extraction" in text


def test_a_blank_project_prompt_falls_back_to_the_built_in(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "crosscheck.txt").write_text("   \n", encoding="utf-8")
    assert "{{schema_md}}" in load_prompt(tmp_path)


# ----- The message the checker sees -----

def test_the_checker_is_not_shown_the_extractors_confidence():
    """Seeing how sure the extractor was anchors the checker onto the answer it should be testing."""
    rows = _rows()
    rows[0]["confidence"] = 9
    rows[0]["reasoning"] = "the paper says so"
    msg = format_record_message(Source(title="P", id=1), rows, "the paper text")
    assert "confidence" not in msg.lower()
    assert "the paper says so" not in msg


def test_the_message_carries_each_value_with_its_quote():
    msg = format_record_message(Source(title="P", id=1), _rows(), "FULLTEXT BODY")
    assert "within-subjects" in msg and "a within-subjects design" in msg
    assert "FULLTEXT BODY" in msg
    assert msg.index("EXTRACTION UNDER REVIEW") < msg.index("FULL TEXT")


def test_a_field_with_no_quote_is_marked_rather_than_dropped():
    rows = [{"id": 1, "field_name": "design", "value": "x", "source_quote": None}]
    assert "(none given)" in format_record_message(Source(title="P", id=1), rows, "body")


# ----- The call -----

def test_check_returns_one_verdict_per_field():
    client = StubClient({"design": _verdict(), "sample_size": _verdict("disagree", "paper says 52", "52")})
    checker = LLMCrossChecker(client)
    out = checker.check(Source(title="P", id=1), "body", _rows(), FIELDS, "PROMPT {{schema_md}}")

    assert out["design"]["verdict"] == "agree"
    assert out["sample_size"]["verdict"] == "disagree"
    assert out["sample_size"]["suggested_value"] == "52"


def test_the_schema_is_rendered_into_the_system_prompt():
    client = StubClient({"design": _verdict(), "sample_size": _verdict()})
    LLMCrossChecker(client).check(Source(title="P", id=1), "body", _rows(), FIELDS, "HEAD\n{{schema_md}}")
    system = client.calls[0]["system"]
    assert "HEAD" in system and "design" in system and "{{schema_md}}" not in system


def test_project_name_and_additional_instructions_reach_the_prompt():
    client = StubClient({"design": _verdict(), "sample_size": _verdict()})
    LLMCrossChecker(client).check(
        Source(title="P", id=1), "body", _rows(), FIELDS,
        "review: {{project_name}}\n{{schema_md}}\n{{additional}}",
        project_name="Dyadic gaze", additional="N means dyads, not participants.",
    )
    system = client.calls[0]["system"]
    assert "Dyadic gaze" in system
    assert "N means dyads, not participants." in system


def test_additional_instructions_survive_a_template_with_no_marker():
    """A hand-written project prompt predating {{additional}} still picks it up."""
    client = StubClient({"design": _verdict(), "sample_size": _verdict()})
    LLMCrossChecker(client).check(
        Source(title="P", id=1), "body", _rows(), FIELDS, "just the rules",
        additional="count dyads",
    )
    assert "count dyads" in client.calls[0]["system"]


def test_an_unknown_verdict_is_rejected():
    client = StubClient({"design": _verdict("looks-fine"), "sample_size": _verdict()})
    with pytest.raises(LLMError):
        LLMCrossChecker(client).check(Source(title="P", id=1), "body", _rows(), FIELDS, "P")


def test_no_rows_means_no_call():
    client = StubClient({})
    assert LLMCrossChecker(client).check(Source(title="P", id=1), "body", [], FIELDS, "P") == {}
    assert client.calls == []


# ----- Same-model guard -----

def _config(**crosscheck):
    return Config(
        project={"name": "t"},
        llm={"provider": "anthropic", "model": "extractor-model"},
        crosscheck=crosscheck,
    )


def test_a_checker_on_the_extractors_model_is_blocked():
    blocked = crosscheck_llm_blocked(_config(llm_enabled=True))
    assert blocked and "same as the extraction model" in blocked


def test_a_different_model_is_allowed():
    cfg = _config(llm_enabled=True, llm={"model": "checker-model"})
    assert crosscheck_llm_blocked(cfg) is None


def test_the_same_model_can_be_forced_deliberately():
    assert crosscheck_llm_blocked(_config(llm_enabled=True, allow_same_model=True)) is None


def test_the_layer_is_blocked_while_disabled():
    blocked = crosscheck_llm_blocked(_config(llm_enabled=False, llm={"model": "checker-model"}))
    assert blocked and "disabled" in blocked


def test_a_missing_checker_model_is_blocked():
    cfg = Config(project={"name": "t"}, crosscheck={"llm_enabled": True})
    blocked = crosscheck_llm_blocked(cfg)
    assert blocked and "No cross-check model" in blocked


# ----- Storage -----

def test_llm_verdicts_become_llm_kind_rows():
    records = llm_verdicts_to_records(
        {"design": {**_verdict("disagree", "nope", "between"), "raw": {"verdict": "disagree"}}},
        source_id=1, target_type="ai", target_id="anthropic:x",
        row_ids={"design": 7}, checker_id="stub:checker-1",
        llm_params={"model": "checker-1"}, prompt_version="v1",
    )
    rec = records[0]
    assert rec.check_kind == "llm"
    assert rec.issue_code is None          # the model's reason is the finding
    assert rec.suggested_value == "between"
    assert rec.target_row_id == 7
    assert rec.confidence == 8


# ----- Calibration: the same check over a quick-test run -----

def _seed_quick_test(project, fields, paper="a within-subjects design in the paper"):
    sid = project.db.insert_source(Source(title="A paper", project_id=project.project_id))
    md = project.root / "data" / "markdown" / f"{sid}.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(paper, encoding="utf-8")
    run_id = project.db.create_test_run(project.project_id, "extraction", 1, "prompt", "criteria")
    project.db.insert_test_extraction(run_id, sid, "include", fields, None)
    return sid, run_id


def test_quick_test_fields_unpack_into_checker_rows():
    from ailr.tasks.crosscheck import _quick_test_rows

    rows = _quick_test_rows({
        "id": 3,
        "fields": [
            {"field": "design", "value": "within", "quote": "q1"},
            {"field": "_flag_check", "value": "x", "quote": None},
        ],
    })
    assert [r["field_name"] for r in rows] == ["design"]   # reserved fields skipped
    assert rows[0] == {"id": 3, "field_name": "design", "value": "within", "source_quote": "q1"}


def test_cross_checking_a_quick_test_run_stores_against_that_run(tmp_project):
    from ailr.crosschecker import LLMCrossChecker
    from ailr.tasks.crosscheck import QuickTestCrossCheckTask, quick_test_target_id

    sid, run_id = _seed_quick_test(tmp_project, [{"field": "design", "value": "within", "quote": "q"}])
    client = StubClient({"design": _verdict("disagree", "quote does not support it")})
    summary = QuickTestCrossCheckTask(tmp_project, LLMCrossChecker(client), run_id).run_for_run()

    assert summary.checked == 1 and summary.findings == 1
    stored = tmp_project.db.get_cross_checks(sid)
    assert len(stored) == 1
    assert stored[0]["stage"] == "quick_test"
    assert stored[0]["target_id"] == quick_test_target_id(run_id)
    # Staleness is an extraction-stage notion; a quick-test finding must never be hidden by it.
    assert stored[0]["stale"] is False


def test_quick_test_findings_do_not_leak_into_the_extraction_badges(tmp_project):
    from ailr.crosschecker import LLMCrossChecker
    from ailr.tasks.crosscheck import QuickTestCrossCheckTask

    sid, run_id = _seed_quick_test(tmp_project, [{"field": "design", "value": "within", "quote": "q"}])
    client = StubClient({"design": _verdict("disagree", "nope")})
    QuickTestCrossCheckTask(tmp_project, LLMCrossChecker(client), run_id).run_for_run()

    assert tmp_project.db.cross_checks_by_field(sid) == {}
    assert tmp_project.db.cross_check_counts([sid]) == {}


def test_field_summary_ranks_the_worst_field_first(tmp_project):
    from ailr.crosschecker import LLMCrossChecker
    from ailr.tasks.crosscheck import QuickTestCrossCheckTask, quick_test_target_id

    _, run_id = _seed_quick_test(tmp_project, [
        {"field": "design", "value": "within", "quote": "q"},
        {"field": "sample_size", "value": 48, "quote": "q2"},
    ])
    client = StubClient({
        "design": _verdict("agree"),
        "sample_size": _verdict("disagree", "paper says 52", "52"),
    })
    QuickTestCrossCheckTask(tmp_project, LLMCrossChecker(client), run_id).run_for_run()

    rows = tmp_project.db.cross_check_field_summary(quick_test_target_id(run_id))
    assert rows[0]["field"] == "sample_size"
    assert rows[0]["flagged"] == 1 and rows[0]["rate"] == 1.0
    assert rows[0]["reasons"] == ["paper says 52"]
    assert rows[1]["field"] == "design" and rows[1]["flagged"] == 0


def test_two_quick_test_runs_keep_separate_findings(tmp_project):
    from ailr.crosschecker import LLMCrossChecker
    from ailr.tasks.crosscheck import QuickTestCrossCheckTask, quick_test_target_id

    sid, run_a = _seed_quick_test(tmp_project, [{"field": "design", "value": "within", "quote": "q"}])
    run_b = tmp_project.db.create_test_run(tmp_project.project_id, "extraction", 1, "p2", "c")
    tmp_project.db.insert_test_extraction(run_b, sid, "include",
                                          [{"field": "design", "value": "between", "quote": "q"}], None)

    for run in (run_a, run_b):
        client = StubClient({"design": _verdict("disagree", f"run {run}")})
        QuickTestCrossCheckTask(tmp_project, LLMCrossChecker(client), run).run_for_run()

    assert len(tmp_project.db.cross_checks_for_target(quick_test_target_id(run_a))) == 1
    assert len(tmp_project.db.cross_checks_for_target(quick_test_target_id(run_b))) == 1


def test_the_two_layers_are_stored_side_by_side(db, tmp_project):
    """check_kind is part of the replace key, so running one layer must not clear the other's rows."""
    sid = db.insert_source(Source(title="A paper", project_id=tmp_project.project_id))
    row_id = db.insert_extraction(ExtractionResult(
        extractor_type="ai", extractor_id="anthropic:x", field_name="design",
        value="within", source_quote="q", source_id=sid,
    ))

    def _rec(kind, verdict):
        return CrossCheckRecord(
            source_id=sid, stage="extraction", target_type="ai", target_id="anthropic:x",
            target_row_id=row_id, field_name="design", checker_type="ai",
            checker_id=f"{kind}-checker", check_kind=kind, verdict=verdict, reason="r",
        )

    db.replace_cross_checks(sid, "extraction", "ai", "anthropic:x", "deterministic",
                            [_rec("deterministic", "agree")])
    db.replace_cross_checks(sid, "extraction", "ai", "anthropic:x", "llm",
                            [_rec("llm", "disagree")])

    kinds = {r["check_kind"] for r in db.get_cross_checks(sid)}
    assert kinds == {"deterministic", "llm"}
    assert db.cross_check_counts([sid]) == {sid: 1}  # only the disagreeing one is open


def test_each_quick_test_run_keeps_its_findings_under_its_own_name(tmp_project):
    """Read back without quick_test_target_id: a constant run id would let run B replace run A's
    findings while each lookup through the helper still found one row."""
    from ailr.crosschecker import LLMCrossChecker
    from ailr.tasks.crosscheck import QuickTestCrossCheckTask

    sid, run_a = _seed_quick_test(tmp_project, [{"field": "design", "value": "within", "quote": "q"}])
    run_b = tmp_project.db.create_test_run(tmp_project.project_id, "extraction", 1, "p2", "c")
    tmp_project.db.insert_test_extraction(run_b, sid, "include",
                                          [{"field": "design", "value": "between", "quote": "q"}], None)
    for run in (run_a, run_b):
        client = StubClient({"design": _verdict("disagree", f"run {run}")})
        QuickTestCrossCheckTask(tmp_project, LLMCrossChecker(client), run).run_for_run()

    stored = sorted((r["target_id"], r["reason"]) for r in tmp_project.db.get_cross_checks(sid))
    assert stored == [(f"run:{run_a}", f"run {run_a}"), (f"run:{run_b}", f"run {run_b}")]


def test_the_guard_reads_each_stages_own_model():
    """A per-stage override decides which model did the work: a checker on that model is blocked
    even though it differs from the top-level one, and only for that stage."""
    cc = {"llm_enabled": True, "llm": {"model": "stage-model"}}
    base = {"project": {"name": "t"}, "llm": {"provider": "anthropic", "model": "default-model"}, "crosscheck": cc}

    extraction = Config(**base, extraction={"llm": {"model": "stage-model"}})
    assert "same as the extraction model" in crosscheck_llm_blocked(extraction)

    screening = Config(**base, screening={"llm": {"model": "stage-model"}})
    assert "same as the abstract model" in crosscheck_llm_blocked(screening, stage="abstract")
    assert crosscheck_llm_blocked(screening, stage="extraction") is None
