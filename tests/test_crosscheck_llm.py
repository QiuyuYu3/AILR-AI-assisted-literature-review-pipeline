"""The LLM cross-check layer: prompt resolution, the call, and how verdicts are stored."""

import pytest

from ailr.core.config import Config, crosscheck_llm_blocked
from ailr.core.crosscheck import CrossCheckRecord, llm_verdicts_to_records
from ailr.core.source import Source
from ailr.crosschecker import LLMCrossChecker, build_tool_schema, format_record_message, load_prompt
from ailr.exceptions import LLMError
from ailr.extraction import FieldSpec
from ailr.llm.base import CallMetadata
from ailr.reviewers import ExtractionResult


class _StubClient:
    """Records what it was asked and returns a canned tool payload."""

    provider_name = "stub"
    model_name = "checker-1"
    temperature = 0.0
    effective_seed = None

    def __init__(self, output):
        self.output = output
        self.calls: list[dict] = []

    def complete_structured(self, *, system, user_message, tool_schema, max_tokens, cache_system=False):
        self.calls.append({"system": system, "user": user_message, "schema": tool_schema})
        return self.output, CallMetadata(provider="stub", model="checker-1", input_tokens=10, output_tokens=5)


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
    client = _StubClient({"design": _verdict(), "sample_size": _verdict("disagree", "paper says 52", "52")})
    checker = LLMCrossChecker(client)
    out = checker.check(Source(title="P", id=1), "body", _rows(), FIELDS, "PROMPT {{schema_md}}")

    assert out["design"]["verdict"] == "agree"
    assert out["sample_size"]["verdict"] == "disagree"
    assert out["sample_size"]["suggested_value"] == "52"


def test_the_schema_is_rendered_into_the_system_prompt():
    client = _StubClient({"design": _verdict(), "sample_size": _verdict()})
    LLMCrossChecker(client).check(Source(title="P", id=1), "body", _rows(), FIELDS, "HEAD\n{{schema_md}}")
    system = client.calls[0]["system"]
    assert "HEAD" in system and "design" in system and "{{schema_md}}" not in system


def test_an_unknown_verdict_is_rejected():
    client = _StubClient({"design": _verdict("looks-fine"), "sample_size": _verdict()})
    with pytest.raises(LLMError):
        LLMCrossChecker(client).check(Source(title="P", id=1), "body", _rows(), FIELDS, "P")


def test_no_rows_means_no_call():
    client = _StubClient({})
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
