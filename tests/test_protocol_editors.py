"""Protocol editors: the criteria and variables GUIs.

Both keep a draft in a dcc.Store and convert between three shapes: the form inputs, the stored
dict, and the markdown the AI is shown. These are all pure functions, and a round trip that
loses information is how a saved criterion or variable quietly changes meaning.
"""

import json

import pytest

from ailr.ui.criteria_view import _render_preview, _specs, _to_content, _to_text
from ailr.ui.template_view import (
    _build_field,
    _compose,
    _field_summary,
    _field_to_form,
    _parse_subfields,
    _serialize_subfields,
    _subkey,
)

# ----- variables editor ----------------------------------------------------------------------


def test_parse_subfields_reads_name_type_and_options():
    subs = _parse_subfields(
        "feature_name: string\n"
        "category: string | options: Vocal, Visual, Verbal\n"
        "performance: text\n"
    )
    assert subs == [
        {"name": "feature_name", "type": "string"},
        {"name": "category", "type": "string", "enum": ["Vocal", "Visual", "Verbal"]},
        {"name": "performance", "type": "string"},   # unknown type falls back to string
    ]


def test_parse_subfields_ignores_blank_and_nameless_lines():
    assert _parse_subfields("\n  \n: string\nok: string\n") == [{"name": "ok", "type": "string"}]


def test_subfields_round_trip_through_text():
    text = "a: string\nb: integer | options: x, y"
    assert _serialize_subfields(_parse_subfields(text)) == text


@pytest.mark.parametrize(
    "ftype,itemtype,enum,subfields,expected",
    [
        ("string", None, "", "", {"type": "string"}),
        ("integer", None, "", "", {"type": "integer"}),
        ("string", None, "lab, home", "", {"type": "string", "enum": ["lab", "home"]}),
        ("list", "string", "", "", {"type": "list", "item_type": "string"}),
        ("group", None, "", "x: string", {"type": "list", "item_type": "object"}),
        ("object", None, "", "x: string", {"type": "object"}),
    ],
)
def test_build_field_maps_the_form_types(ftype, itemtype, enum, subfields, expected):
    f, err = _build_field("thing", ftype, itemtype, "a description", enum, subfields, False)
    assert err is None
    for key, value in expected.items():
        assert f[key] == value
    assert f["description"] == "a description"
    assert "required" not in f          # only written when True


def test_build_field_requires_a_name():
    assert _build_field("", "string", None, "", "", "", False) == (None, "Field name required.")


def test_build_field_requires_subfields_for_group_and_object():
    for ftype in ("group", "object"):
        f, err = _build_field("thing", ftype, None, "", "", "", False)
        assert f is None and "sub-field" in err


@pytest.mark.parametrize(
    "ftype,itemtype,enum,subfields",
    [
        ("string", "string", "lab, home", ""),
        ("list", "integer", "", ""),
        ("group", "string", "", "x: string\ny: integer | options: a, b"),
        ("object", "string", "", "x: string"),
    ],
)
def test_field_to_form_is_the_inverse_of_build_field(ftype, itemtype, enum, subfields):
    built, err = _build_field("thing", ftype, itemtype, "d", enum, subfields, True)
    assert err is None
    form = _field_to_form(built)
    assert form["ftype"] == ftype
    assert form["enum"] == enum
    assert form["subfields"] == subfields
    rebuilt, err = _build_field(
        "thing", form["ftype"], form["itemtype"], "d", form["enum"], form["subfields"], True
    )
    assert err is None and rebuilt == built


def test_subkey_points_at_the_right_container():
    group, _ = _build_field("g", "group", None, "", "", "x: string", False)
    obj, _ = _build_field("o", "object", None, "", "", "x: string", False)
    plain, _ = _build_field("p", "string", None, "", "", "", False)
    assert _subkey(group) == "item_fields"
    assert _subkey(obj) == "fields"
    assert _subkey(plain) is None


def test_field_summary_labels_each_shape():
    group, _ = _build_field("g", "group", None, "", "", "x: string", False)
    obj, _ = _build_field("o", "object", None, "", "", "x: string", False)
    assert _field_summary(group) == "(group, repeating)"
    assert _field_summary(obj) == "(nested object)"
    assert _field_summary({"type": "list", "item_type": "string"}) == "(list of string)"
    assert _field_summary({"type": "string", "enum": ["a", "b"]}) == "(string • a, b)"


def test_compose_lets_a_user_field_override_a_suggested_one_of_the_same_name():
    store = {"include_core": False, "include_suggested": [], "fields": [
        {"name": "study_aim", "type": "string", "description": "mine"},
    ]}
    composed = _compose(store)
    assert [f.name for f in composed] == ["study_aim"]
    assert composed[0].description == "mine"


def test_compose_keeps_user_field_order():
    store = {"include_core": False, "include_suggested": [], "fields": [
        {"name": "b", "type": "string"}, {"name": "a", "type": "string"},
    ]}
    assert [f.name for f in _compose(store)] == ["b", "a"]


# ----- criteria editor -----------------------------------------------------------------------


_ROWS = [
    {"id": "", "name": "Study type", "pass_if": "Empirical study.", "fail_if": "Review.", "uncertain_if": ""},
    {"id": "", "name": "Interaction", "pass_if": "Two humans.", "fail_if": "Human-AI.", "uncertain_if": "Unclear."},
]


def test_specs_tolerates_rows_missing_keys():
    specs = _specs([{"name": "Only a name"}])
    assert specs[0].name == "Only a name"
    assert specs[0].pass_if == "" and specs[0].fail_if == ""


def test_to_content_assigns_stable_ids():
    first = json.loads(_to_content(_ROWS))["criteria"]
    second = json.loads(_to_content(_ROWS))["criteria"]
    assert len(first) == len(_ROWS)
    assert [c["id"] for c in first] == [c["id"] for c in second]
    assert all(c["id"] for c in first)


def test_to_content_preserves_every_rule():
    saved = json.loads(_to_content(_ROWS))["criteria"]
    assert [c["name"] for c in saved] == ["Study type", "Interaction"]
    assert saved[1]["uncertain_if"] == "Unclear."


def test_to_content_keeps_ids_the_user_already_has_and_follows_their_prefix():
    rows = [
        {"id": "B7", "name": "X", "pass_if": "p", "fail_if": "f", "uncertain_if": ""},
        {"id": "", "name": "Y", "pass_if": "p", "fail_if": "f", "uncertain_if": ""},
    ]
    saved = json.loads(_to_content(rows))["criteria"]
    assert saved[0]["id"] == "B7"
    assert saved[1]["id"].startswith("B")


def test_to_text_renders_the_markdown_the_prompt_receives():
    text = _to_text(_to_content(_ROWS))
    assert "Study type" in text
    assert "PASS if: Empirical study." in text
    assert "UNCERTAIN if: Unclear." in text


def test_preview_shows_criterion_ids():
    # Regression: assign_ids is pure, so discarding its return value left the preview (and the
    # saved version snapshot) with blank IDs — while criteria.yaml itself had them. flag_check
    # rows are keyed by criterion_id, so a renumber on restore would silently unlink them.
    preview = _render_preview(_ROWS)
    assert preview.splitlines()[0] == "C1: Study type"
    assert "C2: Interaction" in preview
    assert not preview.startswith(":")


def test_preview_drops_entirely_empty_rows():
    rows = _ROWS + [{"id": "", "name": "  ", "pass_if": "", "fail_if": "", "uncertain_if": ""}]
    assert _render_preview(rows) == _render_preview(_ROWS)
