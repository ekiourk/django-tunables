from collections.abc import Mapping
from typing import Any

import pytest
from django.utils.translation import gettext_lazy as _
from jsonschema import Draft202012Validator, ValidationError

from tests.catalogue import catalogue, pricing, thermostat, weights
from tunables import Catalogue, Float, Group, Integer, Tunable
from tunables.errors import CatalogueError
from tunables.schema import JSON_SCHEMA_DIALECT, describe_group, json_schema, ui_schema

TAGS = {"pricing.vat_rate": ["money"], "pricing.shipping_rates": ["money"], "thermostat.mode": ["comfort"]}


def test_pricing_json_schema() -> None:
    assert json_schema(catalogue, pricing, tags=TAGS) == {
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
                "x-tags": ["money"],
            },
            "free_shipping_over": {
                "type": "number",
                "minimum": 0.0,
                "title": "Free shipping over",
                "default": 50.0,
                "x-unit": "EUR",
                "x-key": "pricing.free_shipping_over",
                "deprecated": False,
                "x-tags": [],
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
                "x-tags": [],
            },
            "shipping_rates": {
                "type": "object",
                "propertyNames": {"type": "string", "enum": ["EUR", "USD", "GBP"]},
                "additionalProperties": {"type": "number", "minimum": 0.0},
                "title": "Shipping rates",
                "default": {"EUR": 4.9},
                "x-unit": "per currency",
                "x-key": "pricing.shipping_rates",
                "deprecated": False,
                "x-tags": ["money"],
            },
            "allow_backorders": {
                "type": "boolean",
                "title": "Allow backorders",
                "default": False,
                "x-unit": "",
                "x-key": "pricing.allow_backorders",
                "deprecated": False,
                "x-tags": [],
            },
        },
        "x-validators": [],
        "x-catalogue-version": catalogue.version,
        "x-category": "shop",
    }


def test_x_tags_default_to_empty_and_sort() -> None:
    properties = json_schema(catalogue, pricing)["properties"]
    assert all(prop["x-tags"] == [] for prop in properties.values())
    tagged = json_schema(catalogue, pricing, tags={"pricing.vat_rate": ["money", "audit"]})["properties"]
    assert tagged["vat_rate"]["x-tags"] == ["audit", "money"]
    assert tagged["currencies"]["x-tags"] == []


def test_json_schema_restricted_to_names() -> None:
    schema = json_schema(catalogue, pricing, tags=TAGS, names={"shipping_rates", "vat_rate"})
    full = json_schema(catalogue, pricing, tags=TAGS)
    assert list(schema["properties"]) == ["vat_rate", "shipping_rates"]
    assert schema["properties"]["vat_rate"] == full["properties"]["vat_rate"]
    assert schema["additionalProperties"] is False
    assert schema["$id"] == full["$id"]
    assert {k: v for k, v in schema.items() if k != "properties"} == {
        k: v for k, v in full.items() if k != "properties"
    }
    assert json_schema(catalogue, weights, names=["beta"])["x-validators"] == ["The three weights must sum to 1."]


def test_ui_schema_restricted_to_names() -> None:
    assert ui_schema(thermostat, names=["display_colour"]) == {
        "type": "VerticalLayout",
        "elements": [{"type": "Control", "scope": "#/properties/display_colour", "label": "Display colour"}],
    }
    assert ui_schema(thermostat, names=["mode", "target_c"]) == {
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
        ],
    }
    assert ui_schema(thermostat, names=[]) == {"type": "VerticalLayout", "elements": []}


def test_describe_group_passes_tags_and_names_through() -> None:
    described = describe_group(catalogue, thermostat, tags=TAGS, names=["mode"])
    assert described["json_schema"] == json_schema(catalogue, thermostat, tags=TAGS, names=["mode"])
    assert described["ui_schema"] == ui_schema(thermostat, names=["mode"])
    assert described["json_schema"]["properties"]["mode"]["x-tags"] == ["comfort"]


def test_document_schema_carries_x_tags() -> None:
    from tunables.schema import document_schema

    groups = document_schema(catalogue, tags=TAGS)["properties"]["groups"]["properties"]
    assert groups["pricing"]["properties"]["vat_rate"]["x-tags"] == ["money"]
    assert groups["thermostat"]["properties"]["target_c"]["x-tags"] == []
    plain = document_schema(catalogue)["properties"]["groups"]["properties"]
    assert plain["pricing"]["properties"]["vat_rate"]["x-tags"] == []


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
            {"type": "Control", "scope": "#/properties/shipping_rates", "label": "Shipping rates"},
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
        [{"title": "A"}],
        ["x"],
    ],
    ids=["unknown", "repeated-across", "repeated-within", "no-tunables-key", "not-a-mapping"],
)
def test_bad_sections_rejected_at_construction(sections: list[dict[str, Any]]) -> None:
    with pytest.raises(CatalogueError):
        Group("g", [Tunable("x", Integer(), 1), Tunable("y", Integer(), 1)], ui={"sections": sections})


def test_describe_group() -> None:
    described_group = describe_group(catalogue, pricing)
    assert set(described_group) == {"json_schema", "ui_schema"}
    assert described_group["json_schema"] == json_schema(catalogue, pricing)
    assert described_group["ui_schema"] == ui_schema(pricing)


def snapshot_validator() -> Draft202012Validator:
    from tunables.schema import document_schema

    schema = document_schema(catalogue)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def defaults() -> dict[str, Any]:
    from datetime import UTC, datetime

    from tunables.document import build_document

    return build_document(catalogue, {}, version=0, created_at=datetime(2026, 9, 5, tzinfo=UTC))


def test_document_schema_accepts_documents_of_this_catalogue() -> None:
    from datetime import UTC, datetime

    from tunables.document import build_document

    validator = snapshot_validator()
    validator.validate(defaults())
    overrides = {"pricing.vat_rate": 0.2, "pricing.currencies": ["USD", "GBP"], "thermostat.mode": "heat"}
    validator.validate(build_document(catalogue, overrides, version=3, created_at=datetime(2026, 9, 5, tzinfo=UTC)))


def test_document_schema_identifies_the_catalogue() -> None:
    from tunables.schema import document_schema

    schema = document_schema(catalogue)
    assert schema["$id"] == f"urn:tunables:snapshot:v1:{catalogue.version}"
    assert schema["x-catalogue-version"] == catalogue.version
    assert schema["properties"]["catalogue_version"] == {"const": catalogue.version}
    assert schema["properties"]["groups"]["required"] == ["pricing", "thermostat", "weights", "limits"]
    assert schema["x-validators"] == ["The number of accepted currencies must not exceed limits.max_currencies."]
    pricing_schema = schema["properties"]["groups"]["properties"]["pricing"]
    assert pricing_schema["required"] == [
        "vat_rate",
        "free_shipping_over",
        "currencies",
        "shipping_rates",
        "allow_backorders",
    ]
    assert "$schema" not in pricing_schema
    assert "$id" not in pricing_schema
    assert "x-catalogue-version" not in pricing_schema
    assert schema["properties"]["overridden"]["items"] == {"enum": list(catalogue.keys())}


def replace_in(document: dict[str, Any], path: list[str], value: Any) -> dict[str, Any]:
    import copy

    document = copy.deepcopy(document)
    target = document
    for step in path[:-1]:
        target = target[step]
    if value is ...:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return document


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (["groups", "pricing", "vat_rate"], "0.2"),
        (["groups", "pricing", "vat_rate"], 7.0),
        (["groups", "thermostat", "mode"], "eco"),
        (["groups", "shop"], {"open": True}),
        (["groups", "pricing", "vat_rate"], ...),
        (["groups", "weights"], ...),
        (["catalogue_version"], "sha256:" + "0" * 64),
        (["overridden"], ["pricing.discount"]),
    ],
    ids=[
        "wrong-type",
        "out-of-range",
        "bad-enum",
        "unknown-group",
        "missing-tunable",
        "missing-group",
        "other-catalogue",
        "unknown-overridden-key",
    ],
)
def test_document_schema_rejects(path: list[str], value: Any) -> None:
    with pytest.raises(ValidationError):
        snapshot_validator().validate(replace_in(defaults(), path, value))


def test_group_schema_carries_the_category() -> None:
    from tunables.schema import document_schema

    assert json_schema(catalogue, pricing)["x-category"] == "shop"
    assert json_schema(catalogue, weights)["x-category"] == "general"
    assert document_schema(catalogue)["properties"]["groups"]["properties"]["thermostat"]["x-category"] == "building"
