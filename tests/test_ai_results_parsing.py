"""Parsing/normalizing AI output — the quiet-data-corruption zone:
- _unwrap_value_quote: messy model payloads (JSON-string lists, over-wrapped items)
  normalize to real values (0.24 multi-select regression)
- _derive_ft_decision: flag_check verdicts -> one full-text decision
- results_import: externally-run AI screening/extraction JSON lands correctly and
  re-imports replace instead of duplicate
- extraction value survives the DB JSON round-trip
"""

import pytest

from ailr.core.source import Source
from ailr.exceptions import LLMError
from ailr.extraction import FieldSpec
from ailr.ingest.results_import import import_ai_results, import_ai_screening_results
from ailr.reviewers import QUOTE_SEPARATOR, ExtractionResult, _unwrap_value_quote
from ailr.tasks.extract import _derive_ft_decision

_LIST_FIELD = FieldSpec(name="study_design", type="list", item_type="string")
_INT_FIELD = FieldSpec(name="n_dyads", type="integer")
_OBJ_FIELD = FieldSpec(name="task", type="object", fields=[FieldSpec(name="name", type="string")])
_OBJ_LIST_FIELD = FieldSpec(
    name="dyadic_features", type="list", item_type="object",
    item_fields=[FieldSpec(name="dyadic_feature_name", type="string")],
)


class TestUnwrapValueQuote:
    def test_multiselect_json_string_is_parsed(self):
        # 0.24 regression: model returned the list as a JSON string
        value, quote = _unwrap_value_quote('["observational", "experimental"]', with_quotes=True, field=_LIST_FIELD)
        assert value == ["observational", "experimental"]
        assert quote is None

    def test_wrapped_list_with_quote(self):
        value, quote = _unwrap_value_quote({"value": ["obs"], "quote": "we observed"}, with_quotes=True, field=_LIST_FIELD)
        assert value == ["obs"] and quote == "we observed"

    def test_wrapped_list_whose_value_is_a_json_string(self):
        value, quote = _unwrap_value_quote({"value": '["a","b"]', "quote": "q"}, with_quotes=True, field=_LIST_FIELD)
        assert value == ["a", "b"] and quote == "q"

    def test_overwrapped_items_flatten_to_values(self):
        raw = [{"value": "a", "quote": "first"}, {"value": "b", "quote": None}]
        value, quote = _unwrap_value_quote(raw, with_quotes=True, field=_LIST_FIELD)
        assert value == ["a", "b"] and quote == "first"

    def test_overwrapped_items_keep_every_quote(self):
        raw = [{"value": "a", "quote": "first"}, {"value": "b", "quote": "second"}]
        value, quote = _unwrap_value_quote(raw, with_quotes=True, field=_LIST_FIELD)
        assert value == ["a", "b"]
        assert quote == f"first{QUOTE_SEPARATOR}second"

    def test_scalar_wrapped_and_bare(self):
        assert _unwrap_value_quote({"value": 24, "quote": "24 dyads"}, with_quotes=True, field=_INT_FIELD) == (24, "24 dyads")
        assert _unwrap_value_quote(24, with_quotes=True, field=_INT_FIELD) == (24, None)

    def test_object_returned_as_json_string_is_parsed(self):
        value, quote = _unwrap_value_quote('{"name": "free play"}', with_quotes=True, field=_OBJ_FIELD)
        assert value == {"name": "free play"} and quote is None

    def test_without_quotes_is_passthrough(self):
        raw = '["untouched"]'
        assert _unwrap_value_quote(raw, with_quotes=False, field=_LIST_FIELD) == (raw, None)


class TestParseJsonBlob:
    """A structured field serialized into a JSON string that does not parse used to be stored as
    that string. It now fails the paper instead — one raise per call site in _unwrap_value_quote."""

    def test_malformed_list_string_raises(self):
        with pytest.raises(LLMError, match="study_design"):
            _unwrap_value_quote("[not json", with_quotes=True, field=_LIST_FIELD)

    def test_malformed_list_string_inside_value_wrapper_raises(self):
        with pytest.raises(LLMError, match="study_design"):
            _unwrap_value_quote({"value": '["a", "b"', "quote": "q"}, with_quotes=True, field=_LIST_FIELD)

    def test_malformed_object_string_raises(self):
        with pytest.raises(LLMError, match="task"):
            _unwrap_value_quote('{"name": "free play"', with_quotes=True, field=_OBJ_FIELD)

    def test_malformed_list_of_object_string_raises(self):
        # The real 0.32 corruption: an unescaped quote inside a long dyadic_features blob.
        raw = '[{"dyadic_feature_name": "the ("quiet moments") measure"}]'
        with pytest.raises(LLMError, match="dyadic_features"):
            _unwrap_value_quote(raw, with_quotes=True, field=_OBJ_LIST_FIELD)

    def test_error_names_the_field_and_tells_you_to_re_run(self):
        with pytest.raises(LLMError, match="Re-run this paper"):
            _unwrap_value_quote("[not json", with_quotes=True, field=_LIST_FIELD)

    def test_plain_string_that_is_not_json_is_left_alone(self):
        # Only strings that open with [ or { are treated as a serialized structure, so a bare
        # "NR" for a list field must not be mistaken for broken JSON.
        assert _unwrap_value_quote("NR", with_quotes=True, field=_LIST_FIELD) == ("NR", None)

    def test_object_string_in_a_scalar_list_slot_is_not_parsed(self):
        # The inner call passes opens="[": after {value, quote} is unwrapped, only an array is a
        # plausible payload for a list-of-scalars field, so a { string stays a string.
        value, quote = _unwrap_value_quote({"value": '{"a": 1}', "quote": "q"}, with_quotes=True, field=_LIST_FIELD)
        assert value == '{"a": 1}' and quote == "q"

    def test_valid_json_string_still_parses(self):
        value, _ = _unwrap_value_quote('[{"dyadic_feature_name": "gaze"}]', with_quotes=True, field=_OBJ_LIST_FIELD)
        assert value == [{"dyadic_feature_name": "gaze"}]


class TestDeriveFtDecision:
    def test_any_fail_is_exclude(self):
        fc = [{"verdict": "PASS"}, {"verdict": "FAIL"}, {"verdict": "UNCERTAIN"}]
        assert _derive_ft_decision(fc) == "exclude"

    def test_uncertain_without_fail_is_uncertain(self):
        assert _derive_ft_decision([{"verdict": "PASS"}, {"verdict": "UNCERTAIN"}]) == "uncertain"

    def test_all_pass_is_include(self):
        assert _derive_ft_decision([{"verdict": "PASS"}, {"verdict": "pass"}]) == "include"

    def test_empty_or_unknown_verdicts_are_uncertain(self):
        assert _derive_ft_decision([]) == "uncertain"
        assert _derive_ft_decision([{"verdict": None}, {"reason": "no verdict key"}]) == "uncertain"


def _add_source(project, title="Paper", doi=None):
    return project.db.insert_source(Source(title=title, doi=doi, project_id=project.project_id))


class TestImportAiScreening:
    def test_records_land_by_source_id_and_doi(self, tmp_project):
        db = tmp_project.db
        sid1 = _add_source(tmp_project, "A")
        sid2 = _add_source(tmp_project, "B", doi="10.1/b")
        summary = import_ai_screening_results(tmp_project, [
            {"source_id": sid1, "decision": "include", "reasoning": "fits", "confidence": 8},
            {"doi": "10.1/B", "decision": "exclude"},  # DOI match is case-insensitive
        ])
        assert summary.imported == 2 and not summary.errors and not summary.unmatched
        assert db.get_latest_ai_decision(sid1, "abstract")["decision"] == "include"
        assert db.get_latest_ai_decisions([sid2], "abstract") == {sid2: "exclude"}

    def test_bad_decision_and_unmatched_are_reported_not_imported(self, tmp_project):
        sid = _add_source(tmp_project)
        summary = import_ai_screening_results(tmp_project, [
            {"source_id": sid, "decision": "maybe"},
            {"source_id": 99999, "decision": "include"},
            "not-a-dict",
        ])
        assert summary.imported == 0
        assert len(summary.errors) == 2  # bad decision + non-dict record
        assert len(summary.unmatched) == 1
        assert tmp_project.db.get_latest_ai_decision(sid, "abstract") is None

    def test_reimport_replaces_instead_of_stacking(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        import_ai_screening_results(tmp_project, [{"source_id": sid, "decision": "include"}])
        import_ai_screening_results(tmp_project, [{"source_id": sid, "decision": "exclude"}])
        assert db.get_latest_ai_decision(sid, "abstract")["decision"] == "exclude"
        assert db.count_screening_decisions(tmp_project.project_id, reviewer_type="ai") == 1


class TestImportAiExtraction:
    def test_fields_and_flag_check_land(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        summary = import_ai_results(tmp_project, [{
            "source_id": sid,
            "extraction": {
                "n_dyads": {"value": 24, "quote": "24 dyads participated"},
                "design": "observational",  # bare value, no quote wrapper
            },
            "flag_check": {"decision": "include"},
        }])
        assert summary.imported == 1 and summary.fields_written == 2 and summary.flags_written == 1
        rows = {r["field_name"]: r for r in db.list_extractions(sid, extractor_type="ai")}
        assert rows["n_dyads"]["value"] == 24
        assert rows["n_dyads"]["source_quote"] == "24 dyads participated"
        assert rows["design"]["value"] == "observational"
        assert rows["design"]["source_quote"] is None
        assert db.get_latest_ai_decision(sid, stage="full_text")["decision"] == "include"

    def test_reimport_replaces_prior_ai_extraction(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        import_ai_results(tmp_project, [{"source_id": sid, "extraction": {"design": "old"}}])
        import_ai_results(tmp_project, [{"source_id": sid, "extraction": {"design": "new"}}])
        rows = db.list_extractions(sid, extractor_type="ai")
        assert [r["value"] for r in rows] == ["new"]

    def test_invalid_flag_decision_is_ignored(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        summary = import_ai_results(tmp_project, [{
            "source_id": sid, "extraction": {"design": "x"}, "flag_check": {"decision": "maybe"},
        }])
        assert summary.flags_written == 0
        assert db.get_latest_ai_decision(sid, stage="full_text") is None


class TestExtractionValueRoundTrip:
    def test_list_and_dict_values_come_back_typed(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        db.insert_extraction(ExtractionResult(
            extractor_type="ai", extractor_id="gpt", field_name="modalities",
            value=["audio", "video"], source_id=sid,
        ))
        db.insert_extraction(ExtractionResult(
            extractor_type="ai", extractor_id="gpt", field_name="task",
            value={"name": "free play", "minutes": 10}, source_id=sid,
        ))
        rows = {r["field_name"]: r["value"] for r in db.list_extractions(sid, extractor_type="ai")}
        assert rows["modalities"] == ["audio", "video"]
        assert rows["task"] == {"name": "free play", "minutes": 10}
