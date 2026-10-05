"""Criteria and variable definitions drafted by an external AI and pasted into Protocol. Anything
these parsers let through becomes a criterion the screening prompt cites by ID, or a variable the
extraction asks for, so a quiet change on the way in changes what the review measures."""

import json

import pytest

from ailr.criteria import load_criteria, save_criteria
from ailr.extraction import compose_schema, save_user_schema
from ailr.ingest.criteria_import import parse_criteria_import
from ailr.ingest.schema_import import parse_schema_import


def _messages(report, level="error"):
    return [f"{i.field}: {i.message}" for i in report.items if i.level == level]


# ----- criteria -----


def _criteria(*rows, wrap=True):
    return json.dumps({"criteria": list(rows)} if wrap else list(rows))


class TestCriteriaImport:
    def test_an_object_or_a_bare_array_is_read(self):
        row = {"id": "I1", "name": "Population", "pass_if": "infants"}
        for raw in (_criteria(row), _criteria(row, wrap=False)):
            rows, report = parse_criteria_import(raw)
            assert [r["id"] for r in rows] == ["I1"] and not report.has_errors

    @pytest.mark.parametrize("raw", ["not json", '{"rules": []}', "42"])
    def test_anything_else_is_one_error_and_no_rows(self, raw):
        rows, report = parse_criteria_import(raw)
        assert rows == [] and len(report.errors) == 1

    def test_unknown_keys_are_dropped_with_a_warning(self):
        rows, report = parse_criteria_import(_criteria({"id": "I1", "name": "P", "rationale": "why"}))
        assert "rationale" not in rows[0]
        assert any("rationale" in m for m in _messages(report, "warning"))

    def test_a_non_object_row_is_an_error(self):
        _, report = parse_criteria_import(_criteria("Population: infants"))
        assert report.has_errors

    def test_ids_are_trimmed(self):
        """Screening matches verdicts to criteria by ID, and the model writes it without the padding."""
        rows, _ = parse_criteria_import(_criteria({"id": " I1 ", "name": "Population"}))
        assert rows[0]["id"] == "I1"

    def test_ids_that_differ_only_in_padding_are_duplicates(self):
        rows, report = parse_criteria_import(_criteria({"id": "I1", "name": "A"}, {"id": "I1 ", "name": "B"}))
        assert [r["name"] for r in rows] == ["A"]
        assert _messages(report) == ["I1: duplicate id"]

    def test_a_null_rule_reads_as_no_rule(self):
        rows, report = parse_criteria_import(_criteria({"id": "I1", "name": "P", "pass_if": "infants", "uncertain_if": None}))
        assert not report.has_errors
        assert rows[0]["uncertain_if"] == ""

    def test_a_wrongly_typed_value_names_its_key(self):
        _, report = parse_criteria_import(_criteria({"id": "I1", "name": ["Population"]}))
        assert any("name" in m.message for m in report.errors)

    def test_an_empty_criterion_is_kept_with_a_warning(self):
        rows, report = parse_criteria_import(_criteria({"id": "I1"}))
        assert len(rows) == 1 and _messages(report, "warning") == ["I1: no name and no PASS rule"]

    def test_imported_rows_save_with_ids_filled_in(self, tmp_path):
        rows, _ = parse_criteria_import(_criteria({"id": "I1", "name": "A"}, {"name": "B"}, {"id": "I3", "name": "C"}))
        path = tmp_path / "criteria.yaml"
        save_criteria(path, rows)
        assert [(c.id, c.name) for c in load_criteria(path).criteria] == [("I1", "A"), ("I2", "B"), ("I3", "C")]


# ----- variables -----


def _fields(*fields, wrap=True):
    return json.dumps({"fields": list(fields)} if wrap else list(fields))


_GROUP = {
    "name": "dyadic_features", "type": "list", "item_type": "object", "description": "features",
    "item_fields": [{"name": "feature", "type": "string", "description": "name"}],
}


class TestSchemaImport:
    def test_an_object_or_a_bare_array_is_read(self):
        f = {"name": "n_dyads", "type": "integer", "description": "dyads"}
        for raw in (_fields(f), _fields(f, wrap=False)):
            fields, report = parse_schema_import(raw)
            assert [x["name"] for x in fields] == ["n_dyads"] and not report.items

    @pytest.mark.parametrize("raw", ["not json", '{"variables": []}', '"x"'])
    def test_anything_else_is_one_error_and_no_fields(self, raw):
        fields, report = parse_schema_import(raw)
        assert fields == [] and len(report.errors) == 1

    def test_unknown_keys_are_dropped_at_every_level(self):
        group = {**_GROUP, "notes": "x", "item_fields": [{"name": "feature", "type": "string", "description": "d", "hint": "y"}]}
        fields, report = parse_schema_import(_fields(group))
        assert "notes" not in fields[0] and "hint" not in fields[0]["item_fields"][0]
        assert len(_messages(report, "warning")) == 2

    def test_a_wrongly_typed_field_names_the_key(self):
        _, report = parse_schema_import(_fields({"name": "n", "type": "count"}))
        assert any(m.message.startswith("type:") for m in report.errors)

    def test_a_reserved_sub_field_name_is_an_error(self):
        group = {**_GROUP, "item_fields": [{"name": "value", "type": "string"}]}
        _, report = parse_schema_import(_fields(group))
        assert report.has_errors

    def test_a_sub_field_that_is_not_an_object_is_an_error(self):
        """Dropping it quietly would load a group with fewer fields than the file defines."""
        group = {**_GROUP, "item_fields": [{"name": "feature", "type": "string"}, "modality"]}
        fields, report = parse_schema_import(_fields(group))
        assert report.has_errors and fields == []

    def test_repeated_sub_field_names_are_an_error(self):
        """The tool schema is keyed by name, so the second would silently replace the first."""
        group = {**_GROUP, "item_fields": [{"name": "feature", "type": "string"}, {"name": "feature", "type": "integer"}]}
        fields, report = parse_schema_import(_fields(group))
        assert report.has_errors and fields == []

    @pytest.mark.parametrize("name", ["", "   "])
    def test_a_blank_name_is_an_error(self, name):
        fields, report = parse_schema_import(_fields({"name": name, "type": "string"}))
        assert report.has_errors and fields == []

    def test_names_are_trimmed_so_padding_cannot_hide_a_duplicate(self):
        fields, report = parse_schema_import(_fields(
            {"name": "n_dyads", "type": "integer", "description": "a"},
            {"name": " n_dyads ", "type": "integer", "description": "b"},
        ))
        assert [f["description"] for f in fields] == ["a"]
        assert _messages(report) == ["n_dyads: duplicate field name"]

    def test_a_leading_underscore_is_reserved(self):
        """Extraction rows named _submitted and _flag_check are bookkeeping, not variables."""
        _, report = parse_schema_import(_fields({"name": "_flag_check", "type": "string"}))
        assert report.has_errors

    def test_soft_problems_are_warnings_only(self):
        fields, report = parse_schema_import(_fields(
            {"name": "n", "type": "integer", "enum": ["1"]},
            {"name": "task", "type": "object"},
            {"name": "groups", "type": "list", "item_type": "object"},
        ))
        assert len(fields) == 3 and not report.has_errors
        warnings = " | ".join(_messages(report, "warning"))
        for text in ("no description", "enum is only used", "object field has no sub-fields", "repeating group has no sub-fields"):
            assert text in warnings

    def test_imported_fields_save_and_compose(self, tmp_path):
        fields, _ = parse_schema_import(_fields({"name": "n_dyads", "type": "integer", "description": "dyads"}, _GROUP))
        path = tmp_path / "schema.yaml"
        save_user_schema(path, False, [], fields)
        composed = {f.name: f for f in compose_schema(path)}
        assert composed["n_dyads"].type == "integer"
        assert [s.name for s in composed["dyadic_features"].item_fields] == ["feature"]
