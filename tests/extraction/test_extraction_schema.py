"""The generated tool schema: what the model is and is not allowed to return.

The nullability of `value` is the contract that matters here. Without it a paper that reports
nothing for a field leaves the model owing a string, and it writes the word "null" into the value,
which then reads as data in exports, in the quote-coverage denominator, and in enum validation.
"""

from ailr.extraction import FieldSpec, build_extraction_tool_schema, compose_schema
from ailr.llm.mock import synth_from_tool_schema


def _props(fields, **kwargs):
    return build_extraction_tool_schema(fields, **kwargs).input_schema["properties"]


def test_a_scalar_value_may_be_null():
    value = _props([FieldSpec(name="total_n", type="integer")])["total_n"]["properties"]["value"]
    assert value["type"] == ["integer", "null"]


def test_an_enum_lists_null_as_an_option_too():
    """type and enum are independent gates: null clears the first only if listed in the second."""
    value = _props([FieldSpec(name="group_size", type="string", enum=["Dyad", "Triad"])])["group_size"]["properties"]["value"]
    assert value["type"] == ["string", "null"]
    assert value["enum"] == ["Dyad", "Triad", None]


def test_a_sub_field_inside_a_list_of_objects_is_nullable_too():
    field = FieldSpec(
        name="dyadic_features", type="list", item_type="object",
        item_fields=[FieldSpec(name="feature", type="string")],
    )
    sub = _props([field])["dyadic_features"]["items"]["properties"]["feature"]["properties"]["value"]
    assert sub["type"] == ["string", "null"]


def test_the_value_stays_required():
    """Required means the key must be present, not that it must be non-null. The model must say
    'null' out loud rather than silently dropping a field."""
    schema = _props([FieldSpec(name="total_n", type="integer")])["total_n"]
    assert "value" in schema["required"]


def test_a_list_of_scalars_says_nothing_with_an_empty_array():
    """No null needed: [] already means 'not reported' and _has_value treats it as empty."""
    field = FieldSpec(name="study_design", type="list", item_type="string", enum=["Primary", "Corpus"])
    value = _props([field])["study_design"]["properties"]["value"]
    assert value["type"] == "array"
    assert value["items"]["enum"] == ["Primary", "Corpus"]


def test_nullability_holds_without_the_quote_wrapper():
    schema = _props([FieldSpec(name="total_n", type="integer")], with_quotes=False)["total_n"]
    assert schema["type"] == ["integer", "null"]


def test_the_quote_slot_is_still_nullable():
    """Deliberate and unchanged: forcing a quote would make the model invent one."""
    quote = _props([FieldSpec(name="total_n", type="integer")])["total_n"]["properties"]["quote"]
    assert quote["type"] == ["string", "null"]


def test_the_mock_client_still_fills_every_slot():
    fields = [
        FieldSpec(name="group_size", type="string", enum=["Dyad", "Triad"]),
        FieldSpec(name="total_n", type="integer"),
    ]
    out = synth_from_tool_schema(build_extraction_tool_schema(fields))
    assert out["group_size"]["value"] == "Dyad"  # the first real option, not null
    assert out["total_n"]["value"] is not None


def test_every_top_level_field_is_required_by_default():
    """The other half of 'never silently drop a field': the key itself must come back."""
    fields = [FieldSpec(name="total_n", type="integer"), FieldSpec(name="design", type="string")]
    assert build_extraction_tool_schema(fields).input_schema["required"] == ["total_n", "design"]


def test_an_object_field_keeps_each_sub_field_and_each_may_be_null():
    field = FieldSpec(name="sample", type="object", fields=[
        FieldSpec(name="n", type="integer"), FieldSpec(name="country", type="string"),
    ])
    schema = _props([field])["sample"]
    assert schema["required"] == ["n", "country"]
    assert schema["properties"]["n"]["properties"]["value"]["type"] == ["integer", "null"]
    assert schema["properties"]["country"]["properties"]["value"]["type"] == ["string", "null"]


def test_confidence_is_asked_once_per_top_level_field_not_per_sub_field():
    nested = FieldSpec(name="sample", type="object", fields=[FieldSpec(name="n", type="integer")])
    props = _props([FieldSpec(name="total_n", type="integer"), nested])
    assert "confidence" in props["total_n"]["required"]
    assert props["total_n"]["properties"]["confidence"]["maximum"] == 10
    assert "confidence" not in props["sample"]["properties"]["n"]["properties"]


def test_skip_verify_in_schema_yaml_marks_only_the_named_fields(tmp_path):
    path = tmp_path / "schema.yaml"
    path.write_text(
        "include_suggested: []\nskip_verify: [design]\nfields:\n"
        "  - name: design\n    type: string\n  - name: total_n\n    type: integer\n",
        encoding="utf-8",
    )
    assert {f.name: f.verify for f in compose_schema(path)} == {"design": False, "total_n": True}
