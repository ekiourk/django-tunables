import json
from collections.abc import Collection, Mapping, Sequence
from importlib import resources
from typing import Any

from tunables.catalogue import Catalogue, Group, Tunable
from tunables.catalogue import validator_description as validator_description

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


Tags = Mapping[str, Sequence[str]]


def json_schema(
    catalogue: Catalogue, group: Group, *, tags: Tags | None = None, names: Collection[str] | None = None
) -> dict[str, Any]:
    """Draft 2020-12 object schema for one group, one property per tunable.

    ``tags`` maps definition keys to tag names and feeds ``x-tags``; ``names`` restricts the properties.
    """
    tags = tags or {}
    tunables = [t for t in group.tunables if names is None or t.name in names]
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
            "properties": {t.name: _property(group, t, tags.get(f"{group.name}.{t.name}", ())) for t in tunables},
            "x-validators": [validator_description(validator) for validator in group.validators],
            "x-catalogue-version": catalogue.version,
            "x-category": group.category,
        }
    )
    return schema


def _property(group: Group, tunable: Tunable, tags: Sequence[str]) -> dict[str, Any]:
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
    prop["x-tags"] = sorted(tags)
    return prop


def ui_schema(group: Group, *, names: Collection[str] | None = None) -> dict[str, Any]:
    """JSON Forms layout: controls in tunable order, grouped by group.ui["sections"] when given.

    ``names`` keeps only those controls; a section left without controls is dropped.
    """
    controls = {t.name: _control(t) for t in group.tunables if names is None or t.name in names}
    elements: list[dict[str, Any]] = []
    for section in group.ui.get("sections", []):
        members = [controls.pop(name) for name in section["tunables"] if name in controls]
        if members:
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


def describe_group(
    catalogue: Catalogue, group: Group, *, tags: Tags | None = None, names: Collection[str] | None = None
) -> dict[str, Any]:
    return {
        "json_schema": json_schema(catalogue, group, tags=tags, names=names),
        "ui_schema": ui_schema(group, names=names),
    }


def document_schema(catalogue: Catalogue, *, tags: Tags | None = None) -> dict[str, Any]:
    """The snapshot envelope schema made specific to this catalogue: every group and tunable typed and required."""
    envelope = json.loads(resources.files("tunables").joinpath("schemas/snapshot-v1.schema.json").read_text())
    groups: dict[str, Any] = {}
    for group in catalogue.groups.values():
        group_schema = json_schema(catalogue, group, tags=tags)
        for key in ("$schema", "$id", "x-catalogue-version"):
            del group_schema[key]
        group_schema["required"] = [tunable.name for tunable in group.tunables]
        groups[group.name] = group_schema
    schema: dict[str, Any] = dict(envelope)
    schema["$id"] = f"urn:tunables:snapshot:v1:{catalogue.version}"
    schema["x-catalogue-version"] = catalogue.version
    schema["x-validators"] = [validator_description(validator) for validator in catalogue.validators]
    schema["properties"] = {
        **envelope["properties"],
        "catalogue_version": {"const": catalogue.version},
        "groups": {
            "type": "object",
            "additionalProperties": False,
            "required": list(catalogue.groups),
            "properties": groups,
        },
        "overridden": {**envelope["properties"]["overridden"], "items": {"enum": list(catalogue.keys())}},
    }
    return schema
