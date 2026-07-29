"""Extraction form: schema -> widgets -> saved rows.

The form is generated from schema.yaml when the page renders and read back through
pattern-matching States when you save, so the two directions have to agree. These tests pin
that mapping, the Save-draft/Submit split, and the guard that refuses to save once the form on
screen and the schema on disk have drifted apart.
"""

from types import SimpleNamespace

import pytest

from ailr.core.source import Source
from ailr.extraction import FieldSpec
from ailr.reviewers import QUOTE_SEPARATOR
from ailr.ui.extract_view import (
    _ROW_KEY,
    _ai_compare_values,
    _ai_data_from_rows,
    _ai_grid_rows,
    _ai_versions,
    _expected_form_keys,
    _flatten_list_item,
    _leaf_widget,
    _missing_form_fields,
    _number_like,
    _save_extraction,
    _split_quotes,
    _strip_nested_quotes,
    _unwrap_cell,
)


def _fields() -> list[FieldSpec]:
    """One field of every shape _field_block knows how to render."""
    return [
        FieldSpec(name="design", type="string"),
        FieldSpec(name="n_dyads", type="integer"),
        FieldSpec(name="modality", type="list", item_type="string"),
        FieldSpec(name="sample", type="object", fields=[
            FieldSpec(name="age", type="string"),
            FieldSpec(name="country", type="string"),
        ]),
        FieldSpec(name="tasks", type="list", item_type="object", item_fields=[
            FieldSpec(name="task_name", type="string"),
            FieldSpec(name="minutes", type="number"),
        ]),
        FieldSpec(name="doi_note", type="string", verify=False),
    ]


_ALL_VALUE_IDS = [{"field": n} for n in ("design", "n_dyads", "modality", "sample.age", "sample.country")]
_ALL_GRID_IDS = [{"field": "tasks"}]


# ----- id expectations -----------------------------------------------------------------------


def test_expected_form_keys_per_field_type():
    values, grids = _expected_form_keys([f for f in _fields() if f.verify])
    assert values == {"design", "n_dyads", "modality", "sample.age", "sample.country"}
    assert grids == {"tasks"}


def test_expected_form_keys_skips_unverified_fields():
    values, _ = _expected_form_keys([f for f in _fields() if f.verify])
    assert "doi_note" not in values


def test_no_missing_fields_when_form_matches_schema():
    assert _missing_form_fields(_fields(), _ALL_VALUE_IDS, _ALL_GRID_IDS) == []


def test_extra_widgets_are_not_reported():
    # A field removed from the schema but still on screen is harmless: the save reads the schema.
    extra = _ALL_VALUE_IDS + [{"field": "gone_from_schema"}]
    assert _missing_form_fields(_fields(), extra, _ALL_GRID_IDS) == []


def test_missing_widgets_are_reported():
    partial = [i for i in _ALL_VALUE_IDS if i["field"] != "sample.country"]
    assert _missing_form_fields(_fields(), partial, []) == ["sample.country", "tasks"]


# ----- leaf widget mapping -------------------------------------------------------------------


def _widget(field: FieldSpec, prefill=None):
    return _leaf_widget(field, dotted=field.name, prefill_cell=prefill).children[2]


def test_enum_field_renders_a_select():
    f = FieldSpec(name="setting", type="string", enum=["lab", "home"])
    w = _widget(f)
    assert type(w).__name__ == "Select"
    assert [o["value"] for o in w.options] == ["lab", "home"]


def test_numeric_field_renders_a_number_input():
    assert _widget(FieldSpec(name="n", type="integer"), prefill=12).type == "number"


def test_numeric_field_falls_back_to_text_for_non_numeric_ai_value():
    # The AI writes things like "NR"; a number input cannot hold that and the browser drops it.
    w = _widget(FieldSpec(name="n", type="integer"), prefill="NR")
    assert w.type == "text"
    assert w.value == "NR"


def test_boolean_field_renders_a_checkbox():
    assert type(_widget(FieldSpec(name="preregistered", type="boolean"))).__name__ == "Checkbox"


def test_plain_string_field_renders_a_textarea():
    assert type(_widget(FieldSpec(name="aim", type="string"))).__name__ == "Textarea"


@pytest.mark.parametrize(
    "value,expected",
    [(None, True), ("", True), (3, True), (3.5, True), ("42", True), ("NR", False), (True, False)],
)
def test_number_like(value, expected):
    assert _number_like(value) is expected


# ----- AI value unwrapping -------------------------------------------------------------------


def test_unwrap_cell_handles_wrapped_and_raw():
    assert _unwrap_cell({"value": 5, "quote": "p1"}) == (5, "p1")
    assert _unwrap_cell(5) == (5, None)
    assert _unwrap_cell({"a": 1}) == ({"a": 1}, None)


def test_strip_nested_quotes_collects_quotes_at_any_depth():
    quotes: list = []
    clean = _strip_nested_quotes(
        {"outer": {"value": [{"inner": {"value": 1, "quote": "q1"}}], "quote": "q0"}}, quotes
    )
    assert clean == {"outer": [{"inner": 1}]}
    assert quotes == ["q0", "q1"]


def test_flatten_list_item_drops_quote_wrappers():
    item_fields = [FieldSpec(name="task_name", type="string"), FieldSpec(name="minutes", type="number")]
    row = {"task_name": {"value": "free play", "quote": "p2"}, "minutes": 5}
    assert _flatten_list_item(row, item_fields) == {"task_name": "free play", "minutes": 5}


def test_split_quotes_unstacks_a_multi_quote_cell():
    assert _split_quotes(f"first{QUOTE_SEPARATOR}second") == ["first", "second"]
    assert _split_quotes(["a", None, ""]) == ["a"]
    assert _split_quotes(None) == []


# ----- "changed from AI" comparison ------------------------------------------------------------


def test_ai_compare_values_flattens_to_widget_shape():
    ai_data = {
        "design": {"value": "observational", "quote": "q"},
        "modality": {"value": ["audio", "video"], "quote": "q"},
        "sample": {"age": {"value": "adults", "quote": "q"}, "country": None},
        "tasks": [{"task_name": {"value": "free play"}}],
    }
    out = _ai_compare_values([f for f in _fields() if f.verify], ai_data)
    assert out["design"] == "observational"
    assert out["modality"] == "audio\nvideo"       # matches the one-item-per-line textarea
    assert out["sample.age"] == "adults"
    assert "sample.country" not in out             # nothing proposed -> nothing to compare
    assert "tasks" not in out                      # ag-grid has no clientside comparison


def test_ai_compare_values_skips_a_list_the_ai_left_empty():
    # An empty list is nothing to propose: it must not become a "" the Use buttons would write
    # over the reviewer's text, nor a reference the "changed from AI" badge compares against.
    out = _ai_compare_values([f for f in _fields() if f.verify], {"modality": {"value": [], "quote": None}})
    assert "modality" not in out


# ----- AI values behind the Use buttons --------------------------------------------------------


def _rows(*pairs) -> list[dict]:
    """Extraction rows as list_extractions returns them."""
    return [
        {"field_name": name, "value": value, "source_quote": quote, "confidence": None}
        for name, value, quote in pairs
    ]


class _FakeDb:
    def __init__(self, runs: list[dict]):
        self._runs = runs

    def list_superseded_ai_runs(self, source_id: int) -> list[dict]:
        return self._runs


_SRC = SimpleNamespace(id=1)


def test_ai_data_from_rows_wraps_everything_but_objects():
    # Only a dict is already in cell shape (an object field carries its quotes at the leaves).
    # Everything else, lists included, gets the {value, quote, confidence} wrapper that holds the
    # row's own source_quote column; _unwrap_cell takes it off again downstream.
    out = _ai_data_from_rows(_rows(
        ("design", "observational", "we observed"),
        ("tasks", [{"task_name": "free play"}], None),
        ("sample", {"age": {"value": "adults", "quote": "q"}}, None),
    ))
    assert out["design"] == {"value": "observational", "quote": "we observed", "confidence": None}
    assert out["tasks"] == {"value": [{"task_name": "free play"}], "quote": None, "confidence": None}
    assert out["sample"] == {"age": {"value": "adults", "quote": "q"}}


def test_ai_data_from_rows_feeds_the_grid_helper():
    # The wrapper above must not hide the rows from _ai_grid_rows.
    ai_data = _ai_data_from_rows(_rows(("tasks", [{"task_name": "free play"}], None)))
    rows = _ai_grid_rows([f for f in _fields() if f.verify], ai_data)["tasks"]
    assert [r["task_name"] for r in rows] == ["free play"]


def test_ai_grid_rows_covers_only_list_of_object_fields():
    ai_data = {"modality": {"value": ["audio"]}, "tasks": [{"task_name": {"value": "free play"}}]}
    out = _ai_grid_rows([f for f in _fields() if f.verify], ai_data)
    assert set(out) == {"tasks"}


def test_ai_grid_rows_flattens_quote_wrappers_and_keys_each_row():
    ai_data = {"tasks": [
        {"task_name": {"value": "free play", "quote": "q"}, "minutes": {"value": 5}},
        {"task_name": {"value": "puzzle"}},
    ]}
    rows = _ai_grid_rows([f for f in _fields() if f.verify], ai_data)["tasks"]
    assert [r["task_name"] for r in rows] == ["free play", "puzzle"]
    assert rows[0]["minutes"] == 5
    # Row keys let "delete selected" tell identical rows apart once they are in the grid.
    assert len({r[_ROW_KEY] for r in rows}) == 2


def test_ai_grid_rows_skips_a_field_the_ai_returned_empty():
    assert _ai_grid_rows([f for f in _fields() if f.verify], {"tasks": []}) == {}


def test_ai_versions_puts_the_current_run_first_then_older_runs():
    db = _FakeDb([
        {"timestamp": "2026-07-28 14:32:01", "rows": _rows(("design", "experimental", None))},
        {"timestamp": "2026-07-21 09:05:44", "rows": _rows(("design", "case study", None))},
    ])
    ai_data = _ai_data_from_rows(_rows(("design", "observational", None)))
    versions, options = _ai_versions(db, _SRC, [f for f in _fields() if f.verify], ai_data)

    assert [o["value"] for o in options] == ["current", "run0", "run1"]
    assert versions["current"]["values"]["design"] == "observational"
    assert versions["run0"]["values"]["design"] == "experimental"
    assert versions["run1"]["values"]["design"] == "case study"
    assert "2026-07-28 14:32:01" in options[1]["label"]


def test_ai_versions_is_empty_without_an_ai_extraction():
    # Drives the fill-all row's visibility: nothing to fill from, so it stays hidden.
    assert _ai_versions(_FakeDb([]), _SRC, _fields(), None) == ({}, [])
    assert _ai_versions(_FakeDb([]), _SRC, _fields(), {}) == ({}, [])


def test_ai_versions_reads_old_runs_through_the_current_schema():
    # An earlier run predates a schema edit: its dropped field is ignored, and a field added
    # since simply has nothing to fill.
    db = _FakeDb([{"timestamp": "2026-07-01 08:00:00", "rows": _rows(
        ("design", "experimental", None),
        ("retired_field", "gone from the schema", None),
    )}])
    ai_data = _ai_data_from_rows(_rows(("design", "observational", None)))
    versions, _ = _ai_versions(db, _SRC, [f for f in _fields() if f.verify], ai_data)
    assert set(versions["run0"]["values"]) == {"design"}
    assert "n_dyads" not in versions["run0"]["values"]


# ----- saving --------------------------------------------------------------------------------


@pytest.fixture
def source(tmp_project):
    sid = tmp_project.db.insert_source(Source(title="Dyadic play", project_id=tmp_project.project_id))
    return tmp_project.db.get_source(sid)


def _do_save(db, src, *, include_autoaccept, ai_rows=None):
    _save_extraction(
        db, src, "amber", _fields(),
        val_values=["RCT", 12, "audio\nvideo", "6mo", "KR"],
        val_ids=list(_ALL_VALUE_IDS),
        quote_values=["p1", None, None, None, None],
        quote_ids=list(_ALL_VALUE_IDS),
        grid_rows=[[{"task_name": "free play", "minutes": 5}]],
        grid_ids=list(_ALL_GRID_IDS),
        ai_rows=ai_rows,
        include_autoaccept=include_autoaccept,
    )
    return {r["field_name"]: r for r in db.list_extractions(src.id, extractor_type="human")}


def test_draft_writes_only_the_fields_a_human_verifies(tmp_project, source):
    saved = _do_save(tmp_project.db, source, include_autoaccept=False)
    assert set(saved) == {"design", "n_dyads", "modality", "sample", "tasks"}


def test_draft_shapes_each_field_type_correctly(tmp_project, source):
    saved = _do_save(tmp_project.db, source, include_autoaccept=False)
    assert saved["design"]["value"] == "RCT"
    assert saved["design"]["source_quote"] == "p1"
    assert saved["modality"]["value"] == ["audio", "video"]          # textarea split on newlines
    assert saved["sample"]["value"] == {                             # object keeps per-sub quotes
        "age": {"value": "6mo", "quote": None},
        "country": {"value": "KR", "quote": None},
    }
    assert saved["tasks"]["value"] == [{"task_name": "free play", "minutes": 5}]


def test_submit_takes_the_ai_value_for_fields_not_flagged_for_verification(tmp_project, source):
    ai_rows = {"doi_note": {"value": "10.1/x", "source_quote": "in the abstract"}}
    saved = _do_save(tmp_project.db, source, include_autoaccept=True, ai_rows=ai_rows)
    assert saved["doi_note"]["value"] == "10.1/x"
    assert saved["doi_note"]["source_quote"] == "in the abstract"
    assert saved["doi_note"]["prompt_version"] == "ai-accepted"
    assert saved["design"]["prompt_version"] == "manual"


def test_submit_skips_unverified_fields_the_ai_never_filled(tmp_project, source):
    saved = _do_save(tmp_project.db, source, include_autoaccept=True, ai_rows={})
    assert "doi_note" not in saved
