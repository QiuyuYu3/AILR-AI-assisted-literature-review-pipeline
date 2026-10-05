"""Imported AI results record the model that produced them, and the methods text names it."""

import json

import pytest

from ailr.exports.methods import build_methods_skeleton
from ailr.ingest.results_import import import_ai_results, import_ai_screening_results
from ailr.reviewers import ExtractionResult
from ailr.ui import extract_view, screen_view
from ailr.ui._common import import_llm_params
from tests.helpers import add_source, callbacks_of, component_text, count_decisions


def _params(db, sql, *args):
    return [json.loads(r["llm_params"]) if r["llm_params"] else None for r in db._conn.execute(sql, args).fetchall()]


@pytest.mark.parametrize("model,temperature,expected", [
    (None, None, None),
    ("  ", 0, None),
    (" claude-x ", None, {"model": "claude-x"}),
    ("claude-x", "", {"model": "claude-x"}),
    ("claude-x", "0.2", {"model": "claude-x", "temperature": 0.2}),
    ("claude-x", 0, {"model": "claude-x", "temperature": 0.0}),
])
def test_the_import_form_becomes_a_model_record(model, temperature, expected):
    assert import_llm_params(model, temperature) == expected


class TestImportedRowsCarryTheModel:
    def test_screening_decisions_and_the_methods_text_name_it(self, tmp_project):
        sid = add_source(tmp_project)
        import_ai_screening_results(tmp_project, [{"source_id": sid, "decision": "include"}],
                                    llm_params={"model": "model-a"})
        assert _params(tmp_project.db, "SELECT llm_params FROM screening_decisions WHERE source_id = ?", sid) == [
            {"model": "model-a"}]
        assert ("screened by model-a (temperature not recorded) and one human reviewer"
                in build_methods_skeleton(tmp_project))

    def test_extraction_rows_and_the_full_text_verdict_name_it(self, tmp_project):
        sid = add_source(tmp_project)
        recorded = {"model": "model-b", "temperature": 0.0}
        import_ai_results(tmp_project, [{"source_id": sid, "extraction": {"design": "obs"},
                                         "flag_check": {"decision": "include"}}], llm_params=recorded)
        db = tmp_project.db
        assert _params(db, "SELECT llm_params FROM extractions WHERE source_id = ? AND extractor_type = 'ai'", sid) == [recorded]
        assert _params(db, "SELECT llm_params FROM screening_decisions WHERE source_id = ? AND stage = 'full_text'",
                       sid) == [recorded]
        text = build_methods_skeleton(tmp_project)
        assert "Structured extraction was performed by model-b (temperature 0.0) using" in text
        assert "assessed by one human reviewer and by model-b (temperature 0.0), both blinded" in text

    def test_records_without_a_full_text_decision_are_listed(self, tmp_project):
        decided, undecided = add_source(tmp_project, "decided"), add_source(tmp_project, "undecided")
        summary = import_ai_results(tmp_project, [
            {"source_id": decided, "extraction": {"design": "x"}, "flag_check": {"decision": "exclude"}},
            {"source_id": undecided, "extraction": {"design": "y"}, "flag_check": {"decision": ""}},
        ])
        assert summary.no_decision == [undecided]


class TestTheMethodsTextNamesWhatProducedTheRows:
    def _extraction_rows(self, project, *llm_params):
        for i, params in enumerate(llm_params):
            project.db.insert_extraction(ExtractionResult(
                extractor_type="ai", extractor_id="x", field_name="design", value="v",
                source_id=add_source(project, f"P{i}"), llm_params=params,
            ))

    def test_rows_with_no_record_are_named_beside_the_recorded_model(self, tmp_project):
        x = {"model": "claude-x", "temperature": 0.0}
        self._extraction_rows(tmp_project, x, x, None)
        assert ("performed by claude-x (temperature 0.0, 2 rows) and a model not recorded in the project (1 row)"
                in build_methods_skeleton(tmp_project))

    def test_a_flag_check_row_is_not_an_unrecorded_model(self, tmp_project):
        self._extraction_rows(tmp_project, {"model": "claude-x", "temperature": 0.0})
        sid = tmp_project.db.list_sources(tmp_project.project_id)[0].id
        tmp_project.db.insert_flag_check(sid, "ai", "x", [{"criterion_id": "C1", "verdict": "PASS"}])
        assert "performed by claude-x (temperature 0.0) using" in build_methods_skeleton(tmp_project)


class TestTheImportFormsAskForTheModel:
    def _file(self, tmp_path, records):
        f = tmp_path / "results.json"
        f.write_text(json.dumps(records), encoding="utf-8")
        return str(f)

    def test_screening_results_are_not_imported_without_it(self, tmp_project, tmp_path):
        db, sid = tmp_project.db, add_source(tmp_project)
        path = self._file(tmp_path, [{"source_id": sid, "decision": "include"}])
        run = callbacks_of(screen_view)["_import_ai_screening"]

        message, _refresh = run(1, path, "", None)
        assert "Enter the model" in component_text(message)
        assert count_decisions(db, tmp_project.project_id, reviewer_type="ai") == 0

        run(1, path, "model-a", 0)
        assert _params(db, "SELECT llm_params FROM screening_decisions WHERE source_id = ?", sid) == [
            {"model": "model-a", "temperature": 0.0}]

    def test_extraction_results_need_it_too_and_papers_left_waiting_are_named(self, tmp_project, tmp_path):
        sid = add_source(tmp_project)
        path = self._file(tmp_path, [{"source_id": sid, "extraction": {"design": "x"}}])
        run = callbacks_of(extract_view)["_import_ai_results"]

        message, _refresh = run(1, path, " ", None)
        assert "Enter the model" in component_text(message)
        assert (sid in tmp_project.db.sources_with_extraction([sid], "ai")) is False

        message, _refresh = run(1, path, "model-b", None)
        text = component_text(message)
        assert f"(#{sid})" in text and "these papers wait for one" in text
