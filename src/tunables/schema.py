import inspect
from typing import Any

from tunables.catalogue import Catalogue, Group, GroupValidator, Tunable

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def json_schema(catalogue: Catalogue, group: Group) -> dict[str, Any]:
    """Draft 2020-12 object schema for one group, one property per tunable."""
    schema: dict[str, Any] = {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": f"urn:tunables:group:{group.name}",
        "title": str(group.title) or group.name,
    }
    if group.description:
        schema["description"] = str(group.description)
    schema.update(
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {tunable.name: _property(group, tunable) for tunable in group.tunables},
            "x-validators": [validator_description(validator) for validator in group.validators],
            "x-catalogue-version": catalogue.version,
        }
    )
    return schema


def _property(group: Group, tunable: Tunable) -> dict[str, Any]:
    prop = tunable.type.json_schema()
    prop["title"] = str(tunable.title) or tunable.name
    if tunable.description:
        prop["description"] = str(tunable.description)
    prop["default"] = tunable.type.to_json(tunable.default)
    prop["x-unit"] = tunable.unit
    prop["x-key"] = f"{group.name}.{tunable.name}"
    prop["deprecated"] = bool(tunable.deprecated)
    if tunable.deprecated:
        prop["x-deprecated-reason"] = tunable.deprecated
    return prop


def validator_description(validator: GroupValidator) -> str:
    description = getattr(validator, "description", None)
    if description:
        return str(description)
    doc = inspect.getdoc(validator)
    if doc:
        return doc.split("\n\n", 1)[0]
    return getattr(validator, "__name__", type(validator).__name__)


def ui_schema(group: Group) -> dict[str, Any]:
    """JSON Forms layout: controls in tunable order, grouped by group.ui["sections"] when given."""
    controls = {tunable.name: _control(tunable) for tunable in group.tunables}
    elements: list[dict[str, Any]] = []
    for section in group.ui.get("sections", []):
        members = [controls.pop(name) for name in section["tunables"]]
        elements.append({"type": "Group", "label": str(section.get("title", "")), "elements": members})
    elements.extend(controls.values())
    return {"type": "VerticalLayout", "elements": elements}


def _control(tunable: Tunable) -> dict[str, Any]:
    control: dict[str, Any] = {
        "type": "Control",
        "scope": f"#/properties/{tunable.name}",
        "label": str(tunable.title) or tunable.name,
    }
    options = dict(tunable.ui)
    if tunable.deprecated:
        options.setdefault("readonly", True)
    if options:
        control["options"] = options
    return control


def describe_group(catalogue: Catalogue, group: Group) -> dict[str, Any]:
    return {"json_schema": json_schema(catalogue, group), "ui_schema": ui_schema(group)}
