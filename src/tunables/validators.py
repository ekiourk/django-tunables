"""Ready-made group validators, and the decorator that sets a validator's description."""

from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from tunables.catalogue import Group, GroupValidator
from tunables.errors import CatalogueError, ConstraintError
from tunables.types import Float, Integer

F = TypeVar("F", bound=Callable[..., Any])


def describes(text: str) -> Callable[[F], F]:
    """Set the description a validator reports in `x-validators`, returning the function unchanged."""

    def decorate(validator: F) -> F:
        validator.description = text  # type: ignore[attr-defined]
        return validator

    return decorate


def _number(value: float) -> str:
    """1.0 as '1', 0.25 as '0.25', for the numbers in messages."""
    return str(int(value)) if float(value).is_integer() else str(value)


def _listed(names: tuple[str, ...]) -> str:
    return f"{', '.join(names[:-1])} and {names[-1]}"


class _NamedValidator:
    """Base for the built-in validators: knows the names it reads, so a Group can check them early."""

    names: tuple[str, ...]
    description: str

    def check_group(self, group: Group) -> None:
        """Raise CatalogueError when a name is missing from the group or is not a number."""
        types = {tunable.name: tunable.type for tunable in group.tunables}
        for name in self.names:
            if name not in types:
                raise CatalogueError(f"validator of group {group.name!r} names unknown tunable {name!r}")
            if not isinstance(types[name], Integer | Float):
                raise CatalogueError(f"validator of group {group.name!r} needs a number, but {name!r} is not one")


class _SumsTo(_NamedValidator):
    def __init__(self, total: float, names: tuple[str, ...], tolerance: float) -> None:
        self.names = names
        self.total = total
        self.tolerance = tolerance
        self.description = f"{' + '.join(names)} must sum to {_number(total)}."

    def __call__(self, values: Mapping[str, Any]) -> None:
        actual = sum(values[name] for name in self.names)
        if abs(actual - self.total) > self.tolerance:
            raise ConstraintError(
                "sum", f"{' + '.join(self.names)} must sum to {_number(self.total)}, got {_number(actual)}"
            )


class _Ordered(_NamedValidator):
    def __init__(
        self, names: tuple[str, ...], *, down: bool, strict: bool, floor: float | None, ceiling: float | None
    ) -> None:
        self.names = names
        self.down = down
        self.strict = strict
        self.floor = floor
        self.ceiling = ceiling
        self.description = self._describe()

    def _describe(self) -> str:
        direction = "descend" if self.down else "ascend"
        sentence = f"{_listed(self.names)} {direction}"
        sentence += " strictly" if self.strict else ", equal values allowed"
        for value, name in ((self.floor, self._floor_name()), (self.ceiling, self._ceiling_name())):
            if value is not None:
                sentence += f", and {name} is {_number(value)}"
        return sentence + "."

    def _floor_name(self) -> str:
        return self.names[-1] if self.down else self.names[0]

    def _ceiling_name(self) -> str:
        return self.names[0] if self.down else self.names[-1]

    def __call__(self, values: Mapping[str, Any]) -> None:
        relation = "greater than" if self.down else "less than"
        if not self.strict:
            relation += " or equal to"
        for first, second in zip(self.names, self.names[1:], strict=False):
            left, right = values[first], values[second]
            if not self._ordered(left, right):
                raise ConstraintError(
                    "order", f"{first} must be {relation} {second}, got {_number(left)} and {_number(right)}"
                )
        self._check_pin("floor", self.floor, self._floor_name(), values)
        self._check_pin("ceiling", self.ceiling, self._ceiling_name(), values)

    def _ordered(self, left: float, right: float) -> bool:
        if self.down:
            return left > right if self.strict else left >= right
        return left < right if self.strict else left <= right

    def _check_pin(self, code: str, pinned: float | None, name: str, values: Mapping[str, Any]) -> None:
        if pinned is not None and values[name] != pinned:
            raise ConstraintError(code, f"{name} must be {_number(pinned)}, got {_number(values[name])}")


def sums_to(total: float, first: str, second: str, *rest: str, tolerance: float = 1e-9) -> GroupValidator:
    """The named values must add up to total, within an absolute tolerance. Code 'sum'."""
    return _SumsTo(total, (first, second, *rest), tolerance)


def descending(
    first: str,
    second: str,
    *rest: str,
    strict: bool = True,
    floor: float | None = None,
    ceiling: float | None = None,
) -> GroupValidator:
    """The named values must decrease in the order given. Codes 'order', 'floor', 'ceiling'.

    floor and ceiling pin the last and first values to exactly that number.
    """
    return _Ordered((first, second, *rest), down=True, strict=strict, floor=floor, ceiling=ceiling)


def ascending(
    first: str,
    second: str,
    *rest: str,
    strict: bool = True,
    floor: float | None = None,
    ceiling: float | None = None,
) -> GroupValidator:
    """The named values must increase in the order given. Codes 'order', 'floor', 'ceiling'.

    floor and ceiling pin the first and last values to exactly that number.
    """
    return _Ordered((first, second, *rest), down=False, strict=strict, floor=floor, ceiling=ceiling)
