import math
from typing import Any

import pytest
from django import forms
from django.core.exceptions import ValidationError

from tests.catalogue import HexColour
from tunables.errors import ConstraintError, TypeCoercionError
from tunables.types import Boolean, Enum, Float, Integer, List, Mapping, String, TunableType

COLOURS = Enum(["red", "green", "blue"])


@pytest.mark.parametrize(
    ("tunable_type", "raw", "expected"),
    [
        (Integer(), 30, 30),
        (Integer(), -1, -1),
        (Float(), 1.5, 1.5),
        (Float(), 50, 50.0),
        (Boolean(), True, True),
        (Boolean(), False, False),
        (String(), "hello", "hello"),
        (String(), "", ""),
        (COLOURS, "red", "red"),
        (COLOURS, "violet", "violet"),
        (List(Integer()), [1, 2, 3], [1, 2, 3]),
        (List(Float()), [1, 2.5], [1.0, 2.5]),
        (List(Integer()), [], []),
    ],
)
def test_coerce_accepts_valid_input(tunable_type: TunableType, raw: Any, expected: Any) -> None:
    value = tunable_type.coerce(raw)
    assert value == expected
    assert type(value) is type(expected)


@pytest.mark.parametrize(
    ("tunable_type", "raw"),
    [
        (Integer(), "30"),
        (Integer(), 30.0),
        (Integer(), True),
        (Integer(), None),
        (Float(), "1.5"),
        (Float(), True),
        (Float(), math.nan),
        (Float(), math.inf),
        (Boolean(), "true"),
        (Boolean(), 1),
        (Boolean(), 0),
        (String(), 1),
        (String(), None),
        (COLOURS, 1),
        (List(Integer()), "abc"),
        (List(Integer()), {"a": 1}),
        (List(Integer()), [1, "x"]),
    ],
)
def test_coerce_rejects_invalid_input(tunable_type: TunableType, raw: Any) -> None:
    with pytest.raises(TypeCoercionError) as info:
        tunable_type.coerce(raw)
    assert info.value.code == "type"


def test_list_coercion_error_names_the_item_index() -> None:
    with pytest.raises(TypeCoercionError, match=r"\[1\]"):
        List(Integer()).coerce([1, "x"])


@pytest.mark.parametrize(
    ("tunable_type", "value", "code"),
    [
        (Integer(min=0), -1, "min"),
        (Integer(max=10), 11, "max"),
        (Float(min=0.0), -0.1, "min"),
        (Float(max=1.0), 1.1, "max"),
        (String(min_length=2), "a", "min_length"),
        (String(max_length=3), "abcd", "max_length"),
        (String(pattern=r"^[a-z]+$"), "ABC", "pattern"),
        (COLOURS, "violet", "enum"),
        (List(Integer(), min_items=1), [], "min_items"),
        (List(Integer(), max_items=2), [1, 2, 3], "max_items"),
        (List(Integer(), unique=True), [1, 1], "unique"),
        (List(Integer(min=0)), [1, -1], "min"),
        (List(COLOURS), ["red", "violet"], "enum"),
    ],
)
def test_validate_rejects_with_code(tunable_type: TunableType, value: Any, code: str) -> None:
    with pytest.raises(ConstraintError) as info:
        tunable_type.validate(value)
    assert info.value.code == code


@pytest.mark.parametrize(
    ("tunable_type", "value"),
    [
        (Integer(min=0, max=10), 0),
        (Integer(min=0, max=10), 10),
        (Float(min=0.0, max=1.0), 0.0),
        (Float(min=0.0, max=1.0), 1.0),
        (Boolean(), False),
        (String(min_length=2, max_length=2), "ab"),
        (String(pattern="[a-z]"), "1a1"),
        (COLOURS, "blue"),
        (List(Integer(), min_items=1, max_items=1), [1]),
        (List(Integer(), unique=True), [1, 2]),
        (List(Integer()), []),
    ],
)
def test_validate_accepts_boundaries(tunable_type: TunableType, value: Any) -> None:
    tunable_type.validate(value)


def test_string_pattern_is_unanchored_like_json_schema() -> None:
    String(pattern="[0-9]").validate("abc1")


def test_list_validation_error_names_the_item_index() -> None:
    with pytest.raises(ConstraintError, match=r"\[1\]"):
        List(Integer(min=0)).validate([1, -1])


@pytest.mark.parametrize(
    ("tunable_type", "value"),
    [
        (Integer(), 42),
        (Float(), 0.24),
        (Boolean(), True),
        (String(), "text"),
        (COLOURS, "green"),
        (List(COLOURS), ["red", "blue"]),
        (List(List(Integer())), [[1], [2, 3]]),
    ],
)
def test_round_trip_through_json(tunable_type: TunableType, value: Any) -> None:
    assert tunable_type.coerce(tunable_type.to_json(value)) == value


@pytest.mark.parametrize(
    ("tunable_type", "expected"),
    [
        (Integer(), {"type": "integer"}),
        (Integer(min=0, max=10), {"type": "integer", "minimum": 0, "maximum": 10}),
        (Float(), {"type": "number"}),
        (Float(min=0.0, max=1.0), {"type": "number", "minimum": 0.0, "maximum": 1.0}),
        (Boolean(), {"type": "boolean"}),
        (String(), {"type": "string"}),
        (
            String(min_length=1, max_length=5, pattern="^[a-z]+$"),
            {"type": "string", "minLength": 1, "maxLength": 5, "pattern": "^[a-z]+$"},
        ),
        (COLOURS, {"type": "string", "enum": ["red", "green", "blue"]}),
        (List(Integer()), {"type": "array", "items": {"type": "integer"}}),
        (
            List(COLOURS, min_items=1, max_items=2, unique=True),
            {
                "type": "array",
                "items": {"type": "string", "enum": ["red", "green", "blue"]},
                "minItems": 1,
                "maxItems": 2,
                "uniqueItems": True,
            },
        ),
    ],
)
def test_json_schema(tunable_type: TunableType, expected: dict[str, Any]) -> None:
    assert tunable_type.json_schema() == expected


@pytest.mark.parametrize(
    ("tunable_type", "field_class"),
    [
        (Integer(), forms.IntegerField),
        (Float(), forms.FloatField),
        (Boolean(), forms.BooleanField),
        (String(), forms.CharField),
        (COLOURS, forms.ChoiceField),
        (List(Integer()), forms.JSONField),
    ],
)
def test_form_field_class(tunable_type: TunableType, field_class: type[forms.Field]) -> None:
    assert type(tunable_type.form_field()) is field_class


def test_form_field_passes_kwargs_through() -> None:
    field = Integer().form_field(label="Count", help_text="How many", initial=3)
    assert field.label == "Count"
    assert field.help_text == "How many"
    assert field.initial == 3


def test_integer_form_field_carries_bounds() -> None:
    field = Integer(min=0, max=10).form_field()
    assert isinstance(field, forms.IntegerField)
    assert field.min_value == 0
    assert field.max_value == 10


def test_enum_form_field_carries_choices() -> None:
    field = COLOURS.form_field()
    assert isinstance(field, forms.ChoiceField)
    assert field.choices == [("red", "red"), ("green", "green"), ("blue", "blue")]


def test_boolean_form_field_is_not_required() -> None:
    assert Boolean().form_field().required is False


@pytest.mark.parametrize(
    ("tunable_type", "raw"),
    [
        (String(pattern="^[a-z]+$"), "ABC"),
        (List(Integer(), unique=True), "[1, 1]"),
        (List(Integer(min=0)), "[1, -1]"),
    ],
)
def test_form_field_cleaning_enforces_validate(tunable_type: TunableType, raw: str) -> None:
    with pytest.raises(ValidationError):
        tunable_type.form_field().clean(raw)


@pytest.mark.parametrize(
    ("tunable_type", "expected"),
    [
        (Integer(), {"name": "integer", "params": {"min": None, "max": None}}),
        (Integer(min=1), {"name": "integer", "params": {"min": 1, "max": None}}),
        (Float(max=2.5), {"name": "float", "params": {"min": None, "max": 2.5}}),
        (Boolean(), {"name": "boolean", "params": {}}),
        (
            String(pattern="x"),
            {"name": "string", "params": {"min_length": None, "max_length": None, "pattern": "x"}},
        ),
        (COLOURS, {"name": "enum", "params": {"choices": ["red", "green", "blue"]}}),
        (
            List(Integer(min=0), unique=True),
            {
                "name": "list",
                "params": {
                    "item": {"name": "integer", "params": {"min": 0, "max": None}},
                    "min_items": None,
                    "max_items": None,
                    "unique": True,
                },
            },
        ),
    ],
)
def test_describe(tunable_type: TunableType, expected: dict[str, Any]) -> None:
    assert tunable_type.describe() == expected


def test_custom_type_works_through_the_base_interface() -> None:
    colour = HexColour()
    assert colour.coerce("#FFAA00") == "#ffaa00"
    colour.validate("#ffaa00")
    with pytest.raises(ConstraintError) as info:
        colour.validate("red")
    assert info.value.code == "format"
    assert colour.describe() == {"name": "hex_colour", "params": {}}
    assert List(colour).coerce(["#AABBCC"]) == ["#aabbcc"]


def test_base_type_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        TunableType()  # type: ignore[abstract]


RATES = Mapping(Enum(["EUR", "USD", "GBP"]), Float(min=0.0))


@pytest.mark.parametrize(
    ("tunable_type", "raw", "expected"),
    [
        (RATES, {"EUR": 4.9, "USD": 6}, {"EUR": 4.9, "USD": 6.0}),
        (RATES, {}, {}),
        (Mapping(String(), List(Integer())), {"a": [1, 2]}, {"a": [1, 2]}),
        (Mapping(String(pattern=r"^\d+,\d+$"), Float()), {"3,5": 1.5}, {"3,5": 1.5}),
    ],
)
def test_mapping_coerce_accepts(tunable_type: TunableType, raw: Any, expected: Any) -> None:
    assert tunable_type.coerce(raw) == expected


@pytest.mark.parametrize("raw", [[("EUR", 4.9)], "EUR=4.9", {"EUR": "4.9"}, {1: 4.9}, None])
def test_mapping_coerce_rejects(raw: Any) -> None:
    with pytest.raises(TypeCoercionError) as info:
        RATES.coerce(raw)
    assert info.value.code == "type"


def test_mapping_coercion_error_names_the_key() -> None:
    with pytest.raises(TypeCoercionError, match=r'\["USD"\]'):
        RATES.coerce({"EUR": 4.9, "USD": "six"})


@pytest.mark.parametrize(
    ("tunable_type", "value", "code"),
    [
        (Mapping(String(), Float(), min_entries=1), {}, "min_entries"),
        (Mapping(String(), Float(), max_entries=1), {"a": 1.0, "b": 2.0}, "max_entries"),
        (RATES, {"CHF": 1.0}, "enum"),
        (RATES, {"EUR": -1.0}, "min"),
        (Mapping(String(min_length=2), Float()), {"a": 1.0}, "min_length"),
    ],
)
def test_mapping_validate_rejects_with_code(tunable_type: TunableType, value: Any, code: str) -> None:
    with pytest.raises(ConstraintError) as info:
        tunable_type.validate(value)
    assert info.value.code == code


def test_mapping_validation_error_names_the_key() -> None:
    with pytest.raises(ConstraintError, match=r'\["EUR"\]: must be >= 0.0'):
        RATES.validate({"EUR": -1.0})


def test_mapping_validate_accepts_boundaries() -> None:
    Mapping(String(), Float(), min_entries=1, max_entries=1).validate({"a": 1.0})
    RATES.validate({})


def test_mapping_round_trip() -> None:
    value = {"EUR": 4.9, "GBP": 3.0}
    assert RATES.coerce(RATES.to_json(value)) == value
    nested = Mapping(String(), List(Integer()))
    assert nested.coerce(nested.to_json({"a": [1], "b": [2, 3]})) == {"a": [1], "b": [2, 3]}


def test_mapping_json_schema() -> None:
    assert RATES.json_schema() == {
        "type": "object",
        "propertyNames": {"type": "string", "enum": ["EUR", "USD", "GBP"]},
        "additionalProperties": {"type": "number", "minimum": 0.0},
    }
    assert Mapping(String(), Integer(), min_entries=1, max_entries=3).json_schema() == {
        "type": "object",
        "propertyNames": {"type": "string"},
        "additionalProperties": {"type": "integer"},
        "minProperties": 1,
        "maxProperties": 3,
    }


def test_mapping_form_field() -> None:
    assert type(RATES.form_field()) is forms.JSONField
    with pytest.raises(ValidationError):
        RATES.form_field().clean('{"EUR": -1}')
    assert RATES.form_field().clean('{"EUR": 4.9}') == {"EUR": 4.9}


def test_mapping_describe() -> None:
    assert Mapping(String(), Integer(min=0), max_entries=5).describe() == {
        "name": "mapping",
        "params": {
            "key": {"name": "string", "params": {"min_length": None, "max_length": None, "pattern": None}},
            "value": {"name": "integer", "params": {"min": 0, "max": None}},
            "min_entries": None,
            "max_entries": 5,
        },
    }


def test_mapping_with_a_non_string_key_type_is_rejected_at_declaration() -> None:
    from tunables import Tunable
    from tunables.errors import CatalogueError

    with pytest.raises(CatalogueError):
        Tunable("table", Mapping(Integer(), Integer()), {"1": 2})


class Numeric(TunableType):
    """A key type that turns digit strings into integers, which a Mapping must refuse."""

    name = "numeric"

    def coerce(self, raw: Any) -> int:
        return int(raw)

    def validate(self, value: Any) -> None:
        pass

    def to_json(self, value: Any) -> int:
        return int(value)

    def json_schema(self) -> dict[str, Any]:
        return {"type": "integer"}

    def form_field(self, **kwargs: Any) -> forms.Field:
        return forms.IntegerField(**kwargs)

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "params": {}}


def test_mapping_refuses_a_key_type_that_does_not_produce_strings() -> None:
    with pytest.raises(TypeCoercionError, match="key type must produce strings"):
        Mapping(Numeric(), Integer()).coerce({"1": 2})
