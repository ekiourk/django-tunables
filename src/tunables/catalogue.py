import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from django.utils.functional import Promise

from tunables.types import TunableType

IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")

GroupValidator = Callable[[Mapping[str, Any]], None]


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
        pass  # TODO: check identifier, coerce and validate default


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
        pass  # TODO: check identifier, unique tunable names


class Catalogue:
    def __init__(self, groups: Sequence[Group], label: str = "") -> None:
        self.label = label
        self.groups: Mapping[str, Group] = {group.name: group for group in groups}  # TODO: order, reject duplicates

    def get(self, key: str) -> Tunable:
        raise NotImplementedError

    def group_of(self, key: str) -> Group:
        raise NotImplementedError

    def keys(self) -> Sequence[str]:
        raise NotImplementedError

    def defaults(self) -> dict[str, dict[str, Any]]:
        raise NotImplementedError

    @property
    def version(self) -> str:
        raise NotImplementedError
