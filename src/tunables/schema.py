from typing import Any

from tunables.catalogue import Catalogue, Group

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def json_schema(catalogue: Catalogue, group: Group) -> dict[str, Any]:
    """Draft 2020-12 object schema for one group, one property per tunable."""
    raise NotImplementedError


def ui_schema(group: Group) -> dict[str, Any]:
    """JSON Forms layout: controls in tunable order, grouped by group.ui["sections"] when given."""
    raise NotImplementedError


def describe_group(catalogue: Catalogue, group: Group) -> dict[str, Any]:
    return {"json_schema": json_schema(catalogue, group), "ui_schema": ui_schema(group)}
