import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from django.utils.functional import Promise

from tunables.errors import CatalogueError, ConstraintError, UnknownKey
from tunables.types import TunableType

IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")

GroupValidator = Callable[[Mapping[str, Any]], None]


def _check_identifier(kind: str, name: str) -> None:
    if not IDENTIFIER.match(name):
        raise CatalogueError(f"{kind} name {name!r} must match {IDENTIFIER.pattern}")


@dataclass(frozen=True)
class Tunable:
    name: str
    type: TunableType
    default: Any
    title: str | Promise = ""
    description: str | Promise = ""
    unit: str = ""
    ui: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    deprecated: str = ""

    def __post_init__(self) -> None:
        _check_identifier("tunable", self.name)
        try:
            default = self.type.coerce(self.default)
            self.type.validate(default)
        except ConstraintError as error:
            raise CatalogueError(f"default of {self.name!r} is invalid: {error.message}") from error
        object.__setattr__(self, "default", default)


@dataclass(frozen=True)
class Group:
    name: str
    tunables: Sequence[Tunable]
    title: str | Promise = ""
    description: str | Promise = ""
    order: int = 0
    validators: Sequence[GroupValidator] = ()
    ui: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _check_identifier("group", self.name)
        object.__setattr__(self, "tunables", tuple(self.tunables))
        seen: set[str] = set()
        for tunable in self.tunables:
            if tunable.name in seen:
                raise CatalogueError(f"duplicate tunable {tunable.name!r} in group {self.name!r}")
            seen.add(tunable.name)


class Catalogue:
    def __init__(self, groups: Sequence[Group], label: str = "") -> None:
        self.label = label
        ordered = sorted(groups, key=lambda group: (group.order, group.name))
        by_name: dict[str, Group] = {}
        for group in ordered:
            if group.name in by_name:
                raise CatalogueError(f"duplicate group {group.name!r}")
            by_name[group.name] = group
        self.groups: Mapping[str, Group] = by_name
        self._tunables = {f"{group.name}.{tunable.name}": tunable for group in ordered for tunable in group.tunables}

    def get(self, key: str) -> Tunable:
        try:
            return self._tunables[key]
        except KeyError:
            raise UnknownKey(key) from None

    def group_of(self, key: str) -> Group:
        self.get(key)
        return self.groups[key.partition(".")[0]]

    def keys(self) -> Sequence[str]:
        return list(self._tunables)

    def defaults(self) -> dict[str, dict[str, Any]]:
        return {
            group.name: {tunable.name: tunable.default for tunable in group.tunables} for group in self.groups.values()
        }

    @cached_property
    def version(self) -> str:
        description: dict[str, Any] = {
            "groups": [
                {
                    "name": group.name,
                    "tunables": [
                        {
                            "name": tunable.name,
                            "type": tunable.type.describe(),
                            "default": tunable.type.to_json(tunable.default),
                            "deprecated": bool(tunable.deprecated),
                        }
                        for tunable in group.tunables
                    ],
                }
                for group in self.groups.values()
            ]
        }
        if self.label:
            description["label"] = self.label
        canonical = json.dumps(description, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
