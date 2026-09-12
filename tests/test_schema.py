from collections.abc import Mapping
from typing import Any

import pytest
from django.utils.translation import gettext_lazy as _
from jsonschema import Draft202012Validator, ValidationError

from tests.catalogue import catalogue, pricing, thermostat, weights
from tunables import Catalogue, Float, Group, Integer, Tunable
from tunables.errors import CatalogueError
from tunables.schema import JSON_SCHEMA_DIALECT, describe_group, json_schema, ui_schema


def test_pricing_json_schema() -> None:
    assert json_schema(catalogue, pricing) == {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": "urn:tunables:group:pricing",
        "title": "Pricing",
        "description": "Prices and shipping rules for the web shop.",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "vat_rate": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "title": "VAT rate",
                "default": 0.24,
                "x-unit": "",
                "x-key": "pricing.vat_rate",
                "deprecated": False,
            },
            "free_shipping_over": {
                "type": "number",
                "minimum": 0.0,
                "title": "Free shipping over",
                "default": 50.0,
                "x-unit": "EUR",
                "x-key": "pricing.free_shipping_over",
                "deprecated": False,
            },
            "currencies": {
                "type": "array",
                "items": {"type": "string", "enum": ["EUR", "USD", "GBP"]},
                "minItems": 1,
                "uniqueItems": True,
                "title": "Accepted currencies",
                "default": ["EUR"],
                "x-unit": "",
                "x-key": "pricing.currencies",
                "deprecated": False,
            },
            "allow_backorders": {
                "type": "boolean",
                "title": "Allow backorders",
                "default": False,
                "x-unit": "",
                "x-key": "pricing.allow_backorders",
                "deprecated": False,
            },
        },
        "x-validators": [],
        "x-catalogue-version": catalogue.version,
    }


@pytest.mark.parametrize("group", list(catalogue.groups.values()), ids=list(catalogue.groups))
def test_group_schema_is_valid_and_accepts_defaults(group: Group) -> None:
    schema = json_schema(catalogue, group)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(catalogue.defaults()[group.name])


def test_group_schema_rejects_bad_values() -> None:
    validator = Draft202012Validator(json_schema(catalogue, pricing))
    with pytest.raises(ValidationError):
        validator.validate({"vat_rate": 2.0})
    with pytest.raises(ValidationError):
        validator.validate({"discount": 1})
    validator.validate({})


def test_deprecated_property() -> None:
    properties = json_schema(catalogue, thermostat)["properties"]
    assert properties["legacy_offset"]["deprecated"] is True
    assert properties["legacy_offset"]["x-deprecated-reason"] == "Use target_c instead."
    assert properties["target_c"]["deprecated"] is False
    assert "x-deprecated-reason" not in properties["target_c"]


def described(values: Mapping[str, Any]) -> None:
    pass


described.description = "Described rule."  # type: ignore[attr-defined]


def bare(values: Mapping[str, Any]) -> None:
    pass


def test_validators_are_described() -> None:
    assert json_schema(catalogue, weights)["x-validators"] == ["The three weights must sum to 1."]
    group = Group("g", [Tunable("x", Integer(), 1)], validators=[described, bare])
    assert json_schema(Catalogue([group]), group)["x-validators"] == ["Described rule.", "bare"]


def test_title_falls_back_to_name_and_lazy_strings_resolve() -> None:
    assert json_schema(catalogue, weights)["properties"]["alpha"]["title"] == "alpha"
    group = Group("g", [Tunable("x", Integer(), 1, title=_("Lazy title"), description=_("Lazy words"))], title=_("G"))
    schema = json_schema(Catalogue([group]), group)
    assert schema["title"] == "G"
    assert type(schema["title"]) is str
    assert schema["properties"]["x"]["title"] == "Lazy title"
    assert schema["properties"]["x"]["description"] == "Lazy words"
    assert type(schema["properties"]["x"]["description"]) is str
    assert "description" not in json_schema(catalogue, weights)["properties"]["alpha"]
    assert "description" not in json_schema(catalogue, weights)


def test_pricing_ui_schema() -> None:
    assert ui_schema(pricing) == {
        "type": "VerticalLayout",
        "elements": [
            {"type": "Control", "scope": "#/properties/vat_rate", "label": "VAT rate"},
            {"type": "Control", "scope": "#/properties/free_shipping_over", "label": "Free shipping over"},
            {"type": "Control", "scope": "#/properties/currencies", "label": "Accepted currencies"},
            {"type": "Control", "scope": "#/properties/allow_backorders", "label": "Allow backorders"},
        ],
    }


def test_ui_options_and_deprecated_readonly() -> None:
    group = Group(
        "g",
        [
            Tunable("plain", Float(), 1.0, ui={"widget": "slider", "step": 0.5}),
            Tunable("old", Float(), 1.0, deprecated="gone"),
            Tunable("old_editable", Float(), 1.0, deprecated="gone", ui={"readonly": False}),
        ],
    )
    elements = ui_schema(group)["elements"]
    assert elements[0]["options"] == {"widget": "slider", "step": 0.5}
    assert elements[1]["options"] == {"readonly": True}
    assert elements[2]["options"] == {"readonly": False}
    assert elements[0]["label"] == "plain"


def test_sections_layout() -> None:
    assert ui_schema(thermostat) == {
        "type": "VerticalLayout",
        "elements": [
            {
                "type": "Group",
                "label": "Control",
                "elements": [
                    {"type": "Control", "scope": "#/properties/target_c", "label": "Target temperature"},
                    {"type": "Control", "scope": "#/properties/mode", "label": "Mode"},
                ],
            },
            {
                "type": "Group",
                "label": "Sampling",
                "elements": [
                    {"type": "Control", "scope": "#/properties/sample_interval", "label": "Sample interval"},
                ],
            },
            {"type": "Control", "scope": "#/properties/display_colour", "label": "Display colour"},
            {
                "type": "Control",
                "scope": "#/properties/legacy_offset",
                "label": "Legacy offset",
                "options": {"readonly": True},
            },
        ],
    }


@pytest.mark.parametrize(
    "sections",
    [
        [{"title": "A", "tunables": ["x", "nope"]}],
        [{"title": "A", "tunables": ["x"]}, {"title": "B", "tunables": ["x"]}],
        [{"title": "A", "tunables": ["x", "x"]}],
    ],
    ids=["unknown", "repeated-across", "repeated-within"],
)
def test_bad_sections_rejected_at_construction(sections: list[dict[str, Any]]) -> None:
    with pytest.raises(CatalogueError):
        Group("g", [Tunable("x", Integer(), 1), Tunable("y", Integer(), 1)], ui={"sections": sections})


def test_describe_group() -> None:
    described_group = describe_group(catalogue, pricing)
    assert set(described_group) == {"json_schema", "ui_schema"}
    assert described_group["json_schema"] == json_schema(catalogue, pricing)
    assert described_group["ui_schema"] == ui_schema(pricing)
