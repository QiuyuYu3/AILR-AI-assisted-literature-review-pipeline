"""Parsing/normalizing AI output — the quiet-data-corruption zone:
- _unwrap_value_quote: messy model payloads (JSON-string lists, over-wrapped items)
  normalize to real values (0.24 multi-select regression)
- _derive_ft_decision: flag_check verdicts -> one full-text decision
- results_import: externally-run AI screening/extraction JSON lands correctly and
  re-imports replace instead of duplicate
- extraction value survives the DB JSON round-trip
"""

import json

import pytest

from ailr.core.source import Source
from ailr.exceptions import LLMError
from ailr.extraction import FieldSpec
from ailr.ingest.results_import import import_ai_results, import_ai_screening_results
from ailr.reviewers import QUOTE_SEPARATOR, ExtractionResult, ScreeningDecision, _unwrap_value_quote
from ailr.tasks.extract import _derive_ft_decision
from ailr.ui import extract_view, screen_view
from tests.helpers import add_source, callbacks_of, component_text, count_decisions

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


class TestImportAiScreening:
    def test_records_land_by_source_id_and_doi(self, tmp_project):
        db = tmp_project.db
        sid1 = add_source(tmp_project, "A")
        sid2 = add_source(tmp_project, "B", doi="10.1/b")
        summary = import_ai_screening_results(tmp_project, [
            {"source_id": sid1, "decision": "include", "reasoning": "fits", "confidence": 8},
            {"doi": "10.1/B", "decision": "exclude"},  # DOI match is case-insensitive
        ])
        assert summary.imported == 2 and not summary.errors and not summary.unmatched
        assert db.get_latest_ai_decision(sid1, "abstract")["decision"] == "include"
        assert db.get_latest_ai_decisions([sid2], "abstract") == {sid2: "exclude"}

    def test_bad_decision_and_unmatched_are_reported_not_imported(self, tmp_project):
        sid = add_source(tmp_project)
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
        sid = add_source(tmp_project)
        import_ai_screening_results(tmp_project, [{"source_id": sid, "decision": "include"}])
        import_ai_screening_results(tmp_project, [{"source_id": sid, "decision": "exclude"}])
        assert db.get_latest_ai_decision(sid, "abstract")["decision"] == "exclude"
        assert count_decisions(db, tmp_project.project_id, reviewer_type="ai") == 1

    def test_reimport_leaves_an_in_app_ai_verdict_alone(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        db.insert_screening_decision(ScreeningDecision(
            decision="include", reasoning="t", reviewer_type="ai", reviewer_id="anthropic:claude",
            source_id=sid, stage="abstract",
        ))
        import_ai_screening_results(tmp_project, [{"source_id": sid, "decision": "exclude"}])
        import_ai_screening_results(tmp_project, [{"source_id": sid, "decision": "exclude"}])
        assert count_decisions(db, tmp_project.project_id, reviewer_type="ai") == 2


class TestImportAiExtraction:
    def test_fields_and_flag_check_land(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
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
        sid = add_source(tmp_project)
        import_ai_results(tmp_project, [{"source_id": sid, "extraction": {"design": "old"}}])
        import_ai_results(tmp_project, [{"source_id": sid, "extraction": {"design": "new"}}])
        rows = db.list_extractions(sid, extractor_type="ai")
        assert [r["value"] for r in rows] == ["new"]

    def test_the_ai_extraction_it_replaces_is_kept_as_history(self, tmp_project):
        """An in-app run may have cost real API calls; an import retires it rather than deleting it."""
        db = tmp_project.db
        sid = add_source(tmp_project)
        db.insert_extraction(ExtractionResult(
            extractor_type="ai", extractor_id="gpt", field_name="design", value="from the app", source_id=sid))
        import_ai_results(tmp_project, [{"source_id": sid, "extraction": {"design": "imported"}}])
        assert [r["value"] for r in db.list_extractions(sid, extractor_type="ai")] == ["imported"]
        [run] = db.list_superseded_ai_runs(sid)
        assert [(r["extractor_id"], r["value"]) for r in run["rows"]] == [("gpt", "from the app")]

    def test_a_record_with_only_a_doi_is_matched_by_it(self, tmp_project):
        sid = add_source(tmp_project, doi="10.1/match")
        summary = import_ai_results(tmp_project, [{"doi": "10.1/MATCH", "extraction": {"design": "x"}}])
        assert (summary.imported, summary.unmatched) == (1, [])
        assert [r["value"] for r in tmp_project.db.list_extractions(sid, extractor_type="ai")] == ["x"]

    def test_unknown_and_foreign_project_ids_are_reported_not_written(self, tmp_project):
        """On a shared Postgres database another project's source ids are valid rows too."""
        db = tmp_project.db
        other_pid = db.get_or_create_project("another review")
        foreign = db.insert_source(Source(title="Not ours", project_id=other_pid))
        summary = import_ai_results(tmp_project, [
            {"source_id": 99999, "extraction": {"design": "x"}},
            {"source_id": foreign, "extraction": {"design": "x"}, "flag_check": {"decision": "include"}},
        ])
        assert summary.imported == 0 and len(summary.unmatched) == 2
        assert db.list_extractions(foreign, extractor_type="ai") == []
        assert db.get_latest_ai_decision(foreign, stage="full_text") is None

    def test_an_exclude_verdict_and_an_object_field_land(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        summary = import_ai_results(tmp_project, [{
            "source_id": sid,
            "extraction": {"task": {"name": "free play", "minutes": 10}},   # an object, not a {value, quote} wrapper
            "flag_check": {"decision": "exclude"},
        }])
        assert (summary.fields_written, summary.flags_written) == (1, 1)
        [row] = db.list_extractions(sid, extractor_type="ai")
        assert row["value"] == {"name": "free play", "minutes": 10}
        assert db.get_latest_ai_decision(sid, stage="full_text")["decision"] == "exclude"

    def test_reimport_replaces_the_imported_full_text_verdict(self, tmp_project):
        """Two imported verdicts on one paper would leave only MAX(id) to tell them apart; an
        in-app AI verdict from another model is not the import's to replace."""
        db = tmp_project.db
        sid = add_source(tmp_project)
        db.insert_screening_decision(ScreeningDecision(
            decision="include", reasoning="t", reviewer_type="ai", reviewer_id="anthropic:claude",
            source_id=sid, stage="full_text",
        ))
        for verdict in ("include", "exclude"):
            import_ai_results(tmp_project, [{"source_id": sid, "extraction": {"design": "x"},
                                             "flag_check": {"decision": verdict}}])
        rows = db._conn.execute(
            "SELECT reviewer_id, decision FROM screening_decisions WHERE source_id = ? AND stage = 'full_text' "
            "ORDER BY id", (sid,),
        ).fetchall()
        assert [(r["reviewer_id"], r["decision"]) for r in rows] == [("anthropic:claude", "include"), ("imported", "exclude")]

    def test_a_failure_part_way_through_a_record_keeps_the_previous_import(self, tmp_project, monkeypatch):
        db = tmp_project.db
        sid = add_source(tmp_project)
        import_ai_results(tmp_project, [{"source_id": sid, "extraction": {"design": "old", "sample": "old"}}])
        real_insert, calls = type(db).insert_extraction, []

        def insert_then_fail(self, result):
            calls.append(result.field_name)
            if len(calls) == 2:
                raise RuntimeError("connection lost")
            return real_insert(self, result)

        monkeypatch.setattr(type(db), "insert_extraction", insert_then_fail)
        with pytest.raises(RuntimeError):
            import_ai_results(tmp_project, [{"source_id": sid, "extraction": {"design": "new", "sample": "new"}}])
        assert sorted(r["value"] for r in db.list_extractions(sid, extractor_type="ai")) == ["old", "old"]

    def test_invalid_flag_decision_is_ignored(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        summary = import_ai_results(tmp_project, [{
            "source_id": sid, "extraction": {"design": "x"}, "flag_check": {"decision": "maybe"},
        }])
        assert summary.flags_written == 0
        assert db.get_latest_ai_decision(sid, stage="full_text") is None


_IMPORTERS = [
    pytest.param(import_ai_screening_results, {"decision": "include"}, id="screening"),
    pytest.param(import_ai_results, {"extraction": {"design": "x"}}, id="extraction"),
]


def _has_ai_data(db, sid) -> bool:
    return db.get_latest_ai_decision(sid, "abstract") is not None or bool(db.list_extractions(sid, extractor_type="ai"))


@pytest.mark.parametrize(("importer", "payload"), _IMPORTERS)
class TestRecordMatching:
    def test_an_id_and_a_doi_naming_different_papers_import_nothing(self, tmp_project, importer, payload):
        a = add_source(tmp_project, "A", doi="10.1/a")
        b = add_source(tmp_project, "B", doi="10.1/b")

        s = importer(tmp_project, [{"source_id": a, "doi": "10.1/b", **payload}])

        assert s.imported == 0 and s.mismatched == [{"source_id": a, "doi": "10.1/b"}]
        assert not _has_ai_data(tmp_project.db, a) and not _has_ai_data(tmp_project.db, b)

    def test_a_doi_of_another_paper_conflicts_even_when_the_id_has_none(self, tmp_project, importer, payload):
        a = add_source(tmp_project, "A")
        b = add_source(tmp_project, "B", doi="10.1/b")

        s = importer(tmp_project, [{"source_id": a, "doi": "10.1/b", **payload}])

        assert s.imported == 0 and len(s.mismatched) == 1
        assert not _has_ai_data(tmp_project.db, a) and not _has_ai_data(tmp_project.db, b)

    def test_a_doi_unlike_the_papers_own_is_a_mismatch(self, tmp_project, importer, payload):
        a = add_source(tmp_project, "A", doi="10.1/a")

        s = importer(tmp_project, [{"source_id": a, "doi": "10.1/elsewhere", **payload}])

        assert s.imported == 0 and len(s.mismatched) == 1
        assert not _has_ai_data(tmp_project.db, a)

    def test_a_doi_nobody_in_the_project_has_is_no_conflict_for_a_paper_without_one(self, tmp_project, importer, payload):
        a = add_source(tmp_project, "A")

        s = importer(tmp_project, [{"source_id": a, "doi": "10.1/new", **payload}])

        assert (s.imported, s.mismatched) == (1, [])
        assert _has_ai_data(tmp_project.db, a)

    def test_the_same_doi_written_as_a_link_agrees(self, tmp_project, importer, payload):
        a = add_source(tmp_project, "A", doi="10.1/AbC")

        s = importer(tmp_project, [{"source_id": a, "doi": "https://doi.org/10.1/abc", **payload}])

        assert (s.imported, s.mismatched) == (1, [])

    def test_a_doi_link_alone_finds_the_paper(self, tmp_project, importer, payload):
        a = add_source(tmp_project, "A", doi="10.1/a")

        s = importer(tmp_project, [{"doi": "https://doi.org/10.1/A", **payload}])

        assert (s.imported, s.unmatched) == (1, [])
        assert _has_ai_data(tmp_project.db, a)


class TestRepeatedRecords:
    def test_screening_counts_the_paper_once_and_takes_the_last_record(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project, doi="10.1/a")

        s = import_ai_screening_results(tmp_project, [
            {"source_id": sid, "decision": "include"},
            {"doi": "10.1/A", "decision": "uncertain"},
            {"source_id": sid, "decision": "exclude"},
        ])

        assert (s.imported, s.duplicates) == (1, [sid])
        assert db.get_latest_ai_decision(sid, "abstract")["decision"] == "exclude"
        assert count_decisions(db, tmp_project.project_id, reviewer_type="ai") == 1

    def test_a_broken_last_record_is_not_covered_by_an_earlier_one(self, tmp_project):
        sid = add_source(tmp_project)

        s = import_ai_screening_results(tmp_project, [
            {"source_id": sid, "decision": "include"},
            {"source_id": sid, "decision": "maybe"},
        ])

        assert (s.imported, len(s.errors), s.duplicates) == (0, 1, [sid])
        assert tmp_project.db.get_latest_ai_decision(sid, "abstract") is None

    def test_extraction_writes_the_paper_once_from_the_last_record(self, tmp_project):
        """Each write archives the one before, so writing both would leave a history entry for a
        version that never took effect."""
        db = tmp_project.db
        sid = add_source(tmp_project)

        s = import_ai_results(tmp_project, [
            {"source_id": sid, "extraction": {"design": "first"}, "flag_check": {"decision": "include"}},
            {"source_id": sid, "extraction": {"design": "second"}, "flag_check": {"decision": "exclude"}},
        ])

        assert (s.imported, s.duplicates, s.fields_written, s.flags_written) == (1, [sid], 1, 1)
        assert [r["value"] for r in db.list_extractions(sid, extractor_type="ai")] == ["second"]
        assert db.list_superseded_ai_runs(sid) == []
        assert db.get_latest_ai_decision(sid, "full_text")["decision"] == "exclude"


class TestImportMessages:
    def _file(self, tmp_path, records):
        path = tmp_path / "results.json"
        path.write_text(json.dumps(records), encoding="utf-8")
        return str(path)

    def test_screening_import_names_mismatches_and_repeats(self, tmp_project, tmp_path):
        a = add_source(tmp_project, "A", doi="10.1/a")
        b = add_source(tmp_project, "B", doi="10.1/b")
        path = self._file(tmp_path, [
            {"source_id": a, "doi": "10.1/b", "decision": "include"},
            {"source_id": b, "decision": "include"},
            {"source_id": b, "decision": "exclude"},
        ])

        alert, _ = callbacks_of(screen_view)["_import_ai_screening"](1, path, "model-a", None)

        text = component_text(alert)
        assert alert.color == "warning"
        assert f"source_id and DOI name different papers: #{a}" in text
        assert f"named more than once, the last record was imported: #{b}" in text

    def test_extraction_import_names_mismatches_and_repeats(self, tmp_project, tmp_path):
        a = add_source(tmp_project, "A", doi="10.1/a")
        b = add_source(tmp_project, "B", doi="10.1/b")
        path = self._file(tmp_path, [
            {"source_id": a, "doi": "10.1/b", "extraction": {"design": "x"}},
            {"source_id": b, "extraction": {"design": "x"}, "flag_check": {"decision": "include"}},
            {"source_id": b, "extraction": {"design": "y"}, "flag_check": {"decision": "include"}},
        ])

        alert, _ = callbacks_of(extract_view)["_import_ai_results"](1, path, "model-a", None)

        text = component_text(alert)
        assert alert.color == "warning"
        assert f"source_id and DOI name different papers: #{a}" in text
        assert f"named more than once, the last record was imported: #{b}" in text


class TestExtractionValueRoundTrip:
    def test_list_and_dict_values_come_back_typed(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
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
