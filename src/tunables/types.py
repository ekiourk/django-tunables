from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from django import forms


class TunableType(ABC):
    name: ClassVar[str]

    @abstractmethod
    def coerce(self, raw: Any) -> Any:
        """Turn JSON input into the Python value. Raises TypeCoercionError."""

    @abstractmethod
    def validate(self, value: Any) -> None:
        """Check constraints on a coerced value. Raises ConstraintError."""

    @abstractmethod
    def to_json(self, value: Any) -> Any:
        """JSON-serialisable representation of the value."""

    @abstractmethod
    def json_schema(self) -> dict[str, Any]:
        """JSON Schema draft 2020-12 fragment for this type."""

    @abstractmethod
    def form_field(self, **kwargs: Any) -> forms.Field:
        """Django form field enforcing the same constraints as validate()."""

    @abstractmethod
    def describe(self) -> dict[str, Any]:
        """{"name": ..., "params": {...}} with every param present."""


@dataclass(frozen=True)
class Integer(TunableType):
    name: ClassVar[str] = "integer"
    min: int | None = None
    max: int | None = None

    def coerce(self, raw: Any) -> int:
        raise NotImplementedError

    def validate(self, value: Any) -> None:
        raise NotImplementedError

    def to_json(self, value: Any) -> int:
        raise NotImplementedError

    def json_schema(self) -> dict[str, Any]:
        raise NotImplementedError

    def form_field(self, **kwargs: Any) -> forms.Field:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        raise NotImplementedError


@dataclass(frozen=True)
class Float(TunableType):
    name: ClassVar[str] = "float"
    min: float | None = None
    max: float | None = None

    def coerce(self, raw: Any) -> float:
        raise NotImplementedError

    def validate(self, value: Any) -> None:
        raise NotImplementedError

    def to_json(self, value: Any) -> float:
        raise NotImplementedError

    def json_schema(self) -> dict[str, Any]:
        raise NotImplementedError

    def form_field(self, **kwargs: Any) -> forms.Field:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        raise NotImplementedError


@dataclass(frozen=True)
class Boolean(TunableType):
    name: ClassVar[str] = "boolean"

    def coerce(self, raw: Any) -> bool:
        raise NotImplementedError

    def validate(self, value: Any) -> None:
        raise NotImplementedError

    def to_json(self, value: Any) -> bool:
        raise NotImplementedError

    def json_schema(self) -> dict[str, Any]:
        raise NotImplementedError

    def form_field(self, **kwargs: Any) -> forms.Field:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        raise NotImplementedError


@dataclass(frozen=True)
class String(TunableType):
    name: ClassVar[str] = "string"
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None

    def coerce(self, raw: Any) -> str:
        raise NotImplementedError

    def validate(self, value: Any) -> None:
        raise NotImplementedError

    def to_json(self, value: Any) -> str:
        raise NotImplementedError

    def json_schema(self) -> dict[str, Any]:
        raise NotImplementedError

    def form_field(self, **kwargs: Any) -> forms.Field:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        raise NotImplementedError


@dataclass(frozen=True)
class Enum(TunableType):
    name: ClassVar[str] = "enum"
    choices: Sequence[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "choices", tuple(self.choices))

    def coerce(self, raw: Any) -> str:
        raise NotImplementedError

    def validate(self, value: Any) -> None:
        raise NotImplementedError

    def to_json(self, value: Any) -> str:
        raise NotImplementedError

    def json_schema(self) -> dict[str, Any]:
        raise NotImplementedError

    def form_field(self, **kwargs: Any) -> forms.Field:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        raise NotImplementedError


@dataclass(frozen=True)
class List(TunableType):
    name: ClassVar[str] = "list"
    item: TunableType
    min_items: int | None = None
    max_items: int | None = None
    unique: bool = False

    def coerce(self, raw: Any) -> list[Any]:
        raise NotImplementedError

    def validate(self, value: Any) -> None:
        raise NotImplementedError

    def to_json(self, value: Any) -> list[Any]:
        raise NotImplementedError

    def json_schema(self) -> dict[str, Any]:
        raise NotImplementedError

    def form_field(self, **kwargs: Any) -> forms.Field:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        raise NotImplementedError
