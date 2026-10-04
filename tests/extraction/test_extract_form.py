"""Extraction form: schema -> widgets -> saved rows.

The form is generated from schema.yaml when the page renders and read back through
pattern-matching States when you save, so the two directions have to agree. These tests pin
that mapping, the Save-draft/Submit split, and the guard that refuses to save once the form on
screen and the schema on disk have drifted apart.
"""

import pytest

from ailr.core.source import Source
from ailr.extraction import FieldSpec
from ailr.reviewers import QUOTE_SEPARATOR, ExtractionResult
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


def _runs_of(*runs: tuple[str, list[dict]]) -> list[dict]:
    """Retired AI runs as list_superseded_ai_runs returns them, newest first."""
    return [{"timestamp": ts, "rows": rows} for ts, rows in runs]


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
    superseded = _runs_of(
        ("2026-07-28 14:32:01", _rows(("design", "experimental", None))),
        ("2026-07-21 09:05:44", _rows(("design", "case study", None))),
    )
    ai_data = _ai_data_from_rows(_rows(("design", "observational", None)))
    versions, options = _ai_versions(superseded, [f for f in _fields() if f.verify], ai_data)

    assert [o["value"] for o in options] == ["current", "run0", "run1"]
    assert versions["current"]["values"]["design"] == "observational"
    assert versions["run0"]["values"]["design"] == "experimental"
    assert versions["run1"]["values"]["design"] == "case study"
    assert "2026-07-28 14:32:01" in options[1]["label"]


def test_ai_versions_is_empty_without_an_ai_extraction():
    # Drives the fill-all row's visibility: nothing to fill from, so it stays hidden.
    assert _ai_versions([], _fields(), None) == ({}, [])
    assert _ai_versions([], _fields(), {}) == ({}, [])


def test_ai_versions_reads_old_runs_through_the_current_schema():
    # An earlier run predates a schema edit: its dropped field is ignored, and a field added
    # since simply has nothing to fill.
    superseded = _runs_of(("2026-07-01 08:00:00", _rows(
        ("design", "experimental", None),
        ("retired_field", "gone from the schema", None),
    )))
    ai_data = _ai_data_from_rows(_rows(("design", "observational", None)))
    versions, _ = _ai_versions(superseded, [f for f in _fields() if f.verify], ai_data)
    assert set(versions["run0"]["values"]) == {"design"}
    assert "n_dyads" not in versions["run0"]["values"]


# ----- the runs _FakeDb stands in for -----------------------------------------------------------


class TestSupersededRunsAgainstTheRealDb:
    """The tests above hand _ai_versions ready-made runs, so the rule that PRODUCES them was never
    exercised. list_superseded_ai_runs groups on the gap between row timestamps rather than on the
    timestamp itself: one run writes its fields one row at a time and can straddle a boundary.
    """

    def _retired_row(self, db, sid, field_name, value, timestamp):
        row_id = db.insert_extraction(ExtractionResult(
            extractor_type="ai", extractor_id="gpt", field_name=field_name,
            value=value, source_id=sid,
        ))
        db._conn.execute(
            "UPDATE extractions SET extractor_type = 'ai_superseded', timestamp = ? WHERE id = ?",
            (timestamp, row_id),
        )
        db._conn.commit()
        return row_id

    def _source(self, project):
        return project.db.insert_source(Source(title="Dyadic play", project_id=project.project_id))

    def test_rows_straddling_a_minute_boundary_are_one_run(self, tmp_project):
        db = tmp_project.db
        sid = self._source(tmp_project)
        self._retired_row(db, sid, "design", "observational", "2026-07-28 14:31:59")
        self._retired_row(db, sid, "n_dyads", 24, "2026-07-28 14:32:01")

        [run] = db.list_superseded_ai_runs(sid)
        assert {r["field_name"] for r in run["rows"]} == {"design", "n_dyads"}

    def test_runs_minutes_apart_split_newest_first(self, tmp_project):
        db = tmp_project.db
        sid = self._source(tmp_project)
        self._retired_row(db, sid, "design", "case study", "2026-07-21 09:05:44")
        self._retired_row(db, sid, "design", "experimental", "2026-07-28 14:32:01")

        runs = db.list_superseded_ai_runs(sid)
        assert [r["timestamp"] for r in runs] == ["2026-07-28 14:32:01", "2026-07-21 09:05:44"]
        assert [r["rows"][0]["value"] for r in runs] == ["experimental", "case study"]

    def test_the_gap_boundary_is_inclusive(self, tmp_project):
        """120s apart is still one slow run; past that it is a human clicking the button again."""
        db = tmp_project.db
        same = self._source(tmp_project)
        self._retired_row(db, same, "design", "a", "2026-07-28 14:00:00")
        self._retired_row(db, same, "n_dyads", 1, "2026-07-28 14:02:00")
        assert len(db.list_superseded_ai_runs(same)) == 1

        split = self._source(tmp_project)
        self._retired_row(db, split, "design", "a", "2026-07-28 14:00:00")
        self._retired_row(db, split, "n_dyads", 1, "2026-07-28 14:02:01")
        assert len(db.list_superseded_ai_runs(split)) == 2

    def test_the_live_ai_extraction_is_not_history(self, tmp_project):
        db = tmp_project.db
        sid = self._source(tmp_project)
        db.insert_extraction(ExtractionResult(
            extractor_type="ai", extractor_id="gpt", field_name="design",
            value="observational", source_id=sid,
        ))
        assert db.list_superseded_ai_runs(sid) == []

    def test_a_source_without_history_has_no_runs(self, tmp_project):
        assert tmp_project.db.list_superseded_ai_runs(self._source(tmp_project)) == []

    def test_the_real_rows_drive_the_version_picker(self, tmp_project):
        """The contract the fixtures above assert by fiat: what the DB returns is what
        _ai_versions reads."""
        db = tmp_project.db
        sid = self._source(tmp_project)
        self._retired_row(db, sid, "design", "case study", "2026-07-21 09:05:44")
        self._retired_row(db, sid, "design", "experimental", "2026-07-28 14:32:01")
        ai_data = _ai_data_from_rows(_rows(("design", "observational", None)))

        versions, options = _ai_versions(
            db.list_superseded_ai_runs(sid), [f for f in _fields() if f.verify], ai_data)

        assert [o["value"] for o in options] == ["current", "run0", "run1"]
        assert versions["current"]["values"]["design"] == "observational"
        assert versions["run0"]["values"]["design"] == "experimental"
        assert versions["run1"]["values"]["design"] == "case study"
        assert "2026-07-28 14:32:01" in options[1]["label"]


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


def test_draft_keeps_sub_quotes_and_drops_row_keys_and_blank_rows(tmp_project, source):
    """The grid hands back an internal _rid per row and an empty row for every line added but not
    filled; neither is data. A row key left in would make two reviewers disagree on every list of
    objects, and the object's quotes are keyed by the dotted sub-field id."""
    _save_extraction(
        tmp_project.db, source, "amber", _fields(),
        val_values=["RCT", 12, "audio", "6mo", "KR"],
        val_ids=list(_ALL_VALUE_IDS),
        quote_values=["p1", None, None, "aged six months", "recruited in Korea"],
        quote_ids=list(_ALL_VALUE_IDS),
        grid_rows=[[{"_rid": "x1", "task_name": "free play", "minutes": 5},
                    {"_rid": "x2", "task_name": "", "minutes": None}]],
        grid_ids=list(_ALL_GRID_IDS),
        include_autoaccept=False,
    )
    saved = {r["field_name"]: r["value"] for r in tmp_project.db.list_extractions(source.id, extractor_type="human")}
    assert saved["sample"] == {
        "age": {"value": "6mo", "quote": "aged six months"},
        "country": {"value": "KR", "quote": "recruited in Korea"},
    }
    assert saved["tasks"] == [{"task_name": "free play", "minutes": 5}]


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
