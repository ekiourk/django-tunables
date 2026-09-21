import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from django.utils.functional import Promise

from tunables.errors import CatalogueError, ConstraintError, UnknownKey
from tunables.identifiers import IDENTIFIER, TAG
from tunables.types import TunableType

GroupValidator = Callable[[Mapping[str, Any]], None]
CatalogueValidator = Callable[[Mapping[str, Mapping[str, Any]]], None]


def _check_identifier(kind: str, name: str) -> None:
    if not IDENTIFIER.match(name):
        raise CatalogueError(f"{kind} name {name!r} must match {IDENTIFIER.pattern}")


def _check_json(kind: str, name: str, field_name: str, value: Mapping[str, Any]) -> None:
    try:
        json.dumps(dict(value), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise CatalogueError(f"{field_name} of {kind} {name!r} is not JSON serialisable: {error}") from error


@dataclass(frozen=True)
class Category:
    """A navigation level above groups. Carries no values and no validators."""

    name: str
    title: str | Promise = ""
    description: str | Promise = ""
    order: int = 0

    def __post_init__(self) -> None:
        _check_identifier("category", self.name)


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
    tags: Sequence[str] = ()

    def __post_init__(self) -> None:
        _check_identifier("tunable", self.name)
        _check_json("tunable", self.name, "metadata", self.metadata)
        _check_json("tunable", self.name, "ui", self.ui)
        object.__setattr__(self, "tags", tuple(self.tags))
        for tag in self.tags:
            if not TAG.match(tag):
                raise CatalogueError(f"tag {tag!r} on tunable {self.name!r} must match {TAG.pattern}")
        if len(set(self.tags)) != len(self.tags):
            raise CatalogueError(f"tunable {self.name!r} lists a tag more than once")
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
    category: str = "general"

    def __post_init__(self) -> None:
        _check_identifier("group", self.name)
        _check_identifier("category", self.category)
        _check_json("group", self.name, "metadata", self.metadata)
        _check_json("group", self.name, "ui", self.ui)
        object.__setattr__(self, "tunables", tuple(self.tunables))
        seen: set[str] = set()
        for tunable in self.tunables:
            if tunable.name in seen:
                raise CatalogueError(f"duplicate tunable {tunable.name!r} in group {self.name!r}")
            seen.add(tunable.name)
        self._check_sections()
        for validator in self.validators:
            check = getattr(validator, "check_group", None)
            if check is not None:
                check(self)

    def _check_sections(self) -> None:
        names = {tunable.name for tunable in self.tunables}
        placed: set[str] = set()
        for section in self.ui.get("sections", []):
            if not isinstance(section, Mapping) or "tunables" not in section:
                raise CatalogueError(f"each section of group {self.name!r} must be a mapping with a 'tunables' list")
            for name in section["tunables"]:
                if name not in names:
                    raise CatalogueError(f"section in group {self.name!r} names unknown tunable {name!r}")
                if name in placed:
                    raise CatalogueError(f"tunable {name!r} appears in more than one section of group {self.name!r}")
                placed.add(name)


class Catalogue:
    def __init__(
        self,
        groups: Sequence[Group],
        *,
        categories: Sequence[Category] = (),
        label: str = "",
        validators: Sequence[CatalogueValidator] = (),
    ) -> None:
        self.label = label
        self.validators: Sequence[CatalogueValidator] = tuple(validators)
        declared: dict[str, Category] = {}
        for category in categories:
            if category.name in declared:
                raise CatalogueError(f"duplicate category {category.name!r}")
            declared[category.name] = category
        declared.setdefault("general", Category("general", title="General"))
        self.categories: Mapping[str, Category] = {
            c.name: c for c in sorted(declared.values(), key=lambda c: (c.order, c.name))
        }
        for group in groups:
            if group.category not in self.categories:
                raise CatalogueError(f"group {group.name!r} names undeclared category {group.category!r}")
        rank = {name: index for index, name in enumerate(self.categories)}
        ordered = sorted(groups, key=lambda g: (rank[g.category], g.order, g.name))
        by_name: dict[str, Group] = {}
        for group in ordered:
            if group.name in by_name:
                raise CatalogueError(f"duplicate group {group.name!r}")
            by_name[group.name] = group
        self.groups: Mapping[str, Group] = by_name
        self._tunables = {f"{group.name}.{tunable.name}": tunable for group in ordered for tunable in group.tunables}

    def groups_in(self, category: str) -> Sequence[Group]:
        """The groups of one category, in catalogue order."""
        if category not in self.categories:
            raise CatalogueError(f"unknown category {category!r}")
        return tuple(group for group in self.groups.values() if group.category == category)

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
        # Groups are hashed in (order, name) order, not category order, so moving a group between
        # categories does not change the version.
        hashed = sorted(self.groups.values(), key=lambda group: (group.order, group.name))
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
                for group in hashed
            ]
        }
        if self.label:
            description["label"] = self.label
        canonical = json.dumps(description, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
