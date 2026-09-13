import math
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from django import forms
from django.core.exceptions import ValidationError

from tunables.errors import ConstraintError, TypeCoercionError


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

    def _form_validator(self, value: Any) -> None:
        try:
            self.validate(self.coerce(value))
        except ConstraintError as error:
            raise ValidationError(error.message, code=error.code) from error

    def _field(self, field_class: type[forms.Field], **kwargs: Any) -> forms.Field:
        kwargs["validators"] = [*kwargs.pop("validators", ()), self._form_validator]
        return field_class(**kwargs)


def _check_bounds(value: Any, minimum: Any, maximum: Any) -> None:
    if minimum is not None and value < minimum:
        raise ConstraintError("min", f"must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ConstraintError("max", f"must be <= {maximum}")


def _bounds_schema(minimum: Any, maximum: Any) -> dict[str, Any]:
    schema: dict[str, Any] = {}
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    return schema


@dataclass(frozen=True)
class Integer(TunableType):
    name: ClassVar[str] = "integer"
    min: int | None = None
    max: int | None = None

    def coerce(self, raw: Any) -> int:
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise TypeCoercionError("expected an integer")
        return raw

    def validate(self, value: Any) -> None:
        _check_bounds(value, self.min, self.max)

    def to_json(self, value: Any) -> int:
        return int(value)

    def json_schema(self) -> dict[str, Any]:
        return {"type": "integer", **_bounds_schema(self.min, self.max)}

    def form_field(self, **kwargs: Any) -> forms.Field:
        return self._field(forms.IntegerField, min_value=self.min, max_value=self.max, **kwargs)

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "params": {"min": self.min, "max": self.max}}


@dataclass(frozen=True)
class Float(TunableType):
    name: ClassVar[str] = "float"
    min: float | None = None
    max: float | None = None

    def coerce(self, raw: Any) -> float:
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            raise TypeCoercionError("expected a number")
        if not math.isfinite(raw):
            raise TypeCoercionError("expected a finite number")
        return float(raw)

    def validate(self, value: Any) -> None:
        _check_bounds(value, self.min, self.max)

    def to_json(self, value: Any) -> float:
        return float(value)

    def json_schema(self) -> dict[str, Any]:
        return {"type": "number", **_bounds_schema(self.min, self.max)}

    def form_field(self, **kwargs: Any) -> forms.Field:
        return self._field(forms.FloatField, min_value=self.min, max_value=self.max, **kwargs)

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "params": {"min": self.min, "max": self.max}}


@dataclass(frozen=True)
class Boolean(TunableType):
    name: ClassVar[str] = "boolean"

    def coerce(self, raw: Any) -> bool:
        if not isinstance(raw, bool):
            raise TypeCoercionError("expected a boolean")
        return raw

    def validate(self, value: Any) -> None:
        pass

    def to_json(self, value: Any) -> bool:
        return bool(value)

    def json_schema(self) -> dict[str, Any]:
        return {"type": "boolean"}

    def form_field(self, **kwargs: Any) -> forms.Field:
        kwargs.setdefault("required", False)
        return self._field(forms.BooleanField, **kwargs)

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "params": {}}


@dataclass(frozen=True)
class String(TunableType):
    name: ClassVar[str] = "string"
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None

    def coerce(self, raw: Any) -> str:
        if not isinstance(raw, str):
            raise TypeCoercionError("expected a string")
        return raw

    def validate(self, value: Any) -> None:
        if self.min_length is not None and len(value) < self.min_length:
            raise ConstraintError("min_length", f"must have at least {self.min_length} characters")
        if self.max_length is not None and len(value) > self.max_length:
            raise ConstraintError("max_length", f"must have at most {self.max_length} characters")
        if self.pattern is not None and re.search(self.pattern, value) is None:
            raise ConstraintError("pattern", f"must match {self.pattern}")

    def to_json(self, value: Any) -> str:
        return str(value)

    def json_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": "string"}
        if self.min_length is not None:
            schema["minLength"] = self.min_length
        if self.max_length is not None:
            schema["maxLength"] = self.max_length
        if self.pattern is not None:
            schema["pattern"] = self.pattern
        return schema

    def form_field(self, **kwargs: Any) -> forms.Field:
        return self._field(forms.CharField, min_length=self.min_length, max_length=self.max_length, **kwargs)

    def describe(self) -> dict[str, Any]:
        params = {"min_length": self.min_length, "max_length": self.max_length, "pattern": self.pattern}
        return {"name": self.name, "params": params}


@dataclass(frozen=True)
class Enum(TunableType):
    name: ClassVar[str] = "enum"
    choices: Sequence[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "choices", tuple(self.choices))

    def coerce(self, raw: Any) -> str:
        if not isinstance(raw, str):
            raise TypeCoercionError("expected a string")
        return raw

    def validate(self, value: Any) -> None:
        if value not in self.choices:
            raise ConstraintError("enum", "must be one of: " + ", ".join(self.choices))

    def to_json(self, value: Any) -> str:
        return str(value)

    def json_schema(self) -> dict[str, Any]:
        return {"type": "string", "enum": list(self.choices)}

    def form_field(self, **kwargs: Any) -> forms.Field:
        return self._field(forms.ChoiceField, choices=[(c, c) for c in self.choices], **kwargs)

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "params": {"choices": list(self.choices)}}


@dataclass(frozen=True)
class List(TunableType):
    name: ClassVar[str] = "list"
    item: TunableType
    min_items: int | None = None
    max_items: int | None = None
    unique: bool = False

    def coerce(self, raw: Any) -> list[Any]:
        if not isinstance(raw, list):
            raise TypeCoercionError("expected a list")
        return [self._coerce_item(index, item) for index, item in enumerate(raw)]

    def _coerce_item(self, index: int, item: Any) -> Any:
        try:
            return self.item.coerce(item)
        except TypeCoercionError as error:
            raise TypeCoercionError(f"[{index}]: {error.message}") from error

    def validate(self, value: Any) -> None:
        if self.min_items is not None and len(value) < self.min_items:
            raise ConstraintError("min_items", f"must have at least {self.min_items} items")
        if self.max_items is not None and len(value) > self.max_items:
            raise ConstraintError("max_items", f"must have at most {self.max_items} items")
        if self.unique and len({self.item.to_json(item) for item in value}) != len(value):
            raise ConstraintError("unique", "items must be unique")
        for index, item in enumerate(value):
            try:
                self.item.validate(item)
            except ConstraintError as error:
                raise ConstraintError(error.code, f"[{index}]: {error.message}") from error

    def to_json(self, value: Any) -> list[Any]:
        return [self.item.to_json(item) for item in value]

    def json_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": "array", "items": self.item.json_schema()}
        if self.min_items is not None:
            schema["minItems"] = self.min_items
        if self.max_items is not None:
            schema["maxItems"] = self.max_items
        if self.unique:
            schema["uniqueItems"] = True
        return schema

    def form_field(self, **kwargs: Any) -> forms.Field:
        return self._field(forms.JSONField, **kwargs)

    def describe(self) -> dict[str, Any]:
        params = {
            "item": self.item.describe(),
            "min_items": self.min_items,
            "max_items": self.max_items,
            "unique": self.unique,
        }
        return {"name": self.name, "params": params}
