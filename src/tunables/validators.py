"""Ready-made group validators, and the decorator that sets a validator's description."""

from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from django.utils.translation import gettext as _

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
    if isinstance(value, int):
        return str(value)
    return str(int(value)) if value.is_integer() else str(value)


def _listed(names: tuple[str, ...]) -> str:
    return f"{', '.join(names[:-1])} and {names[-1]}"


class _NamedValidator:
    """Base for the built-in validators: knows the names it reads, so a Group can check them early."""

    names: tuple[str, ...]
    description: str = ""

    def __str__(self) -> str:
        return self.description

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.description!r})"

    def check_group(self, group: Group) -> None:
        """Raise CatalogueError when a name is missing from the group or is not a number."""
        types = {tunable.name: tunable.type for tunable in group.tunables}
        seen: set[str] = set()
        for name in self.names:
            if name in seen:
                raise CatalogueError(f"validator of group {group.name!r} names {name!r} more than once")
            seen.add(name)
            if name not in types:
                raise CatalogueError(f"validator of group {group.name!r} names unknown tunable {name!r}")
            if not isinstance(types[name], Integer | Float):
                raise CatalogueError(f"validator of group {group.name!r} needs a number, but {name!r} is not one")


def _total(values: Mapping[str, Any], names: tuple[str, ...]) -> float:
    """The sum. Integers add exactly, so one too large for a float reports rather than raising."""
    whole: float = sum(values[name] for name in names if isinstance(values[name], int))
    parts: float = sum(values[name] for name in names if not isinstance(values[name], int))
    try:
        return whole + parts
    except OverflowError:
        return whole


def _within(actual: float, total: float, tolerance: float) -> bool:
    """A value too large for a float is never within tolerance, and must not raise OverflowError."""
    try:
        return abs(actual - total) <= tolerance
    except OverflowError:
        return False


class _SumsTo(_NamedValidator):
    def __init__(self, total: float, names: tuple[str, ...], tolerance: float) -> None:
        self.names = names
        self.total = total
        self.tolerance = tolerance
        self.description = f"{' + '.join(names)} must sum to {_number(total)}."

    def __call__(self, values: Mapping[str, Any]) -> None:
        actual = _total(values, self.names)
        if not _within(actual, self.total, self.tolerance):
            raise ConstraintError(
                "sum",
                _("%(names)s must sum to %(total)s, got %(actual)s")
                % {
                    "names": " + ".join(self.names),
                    "total": _number(self.total),
                    "actual": _number(actual),
                },
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
        for first, second in zip(self.names, self.names[1:], strict=False):
            left, right = values[first], values[second]
            if not self._ordered(left, right):
                raise ConstraintError(
                    "order",
                    self._relation()
                    % {"first": first, "second": second, "left": _number(left), "right": _number(right)},
                )
        self._check_pin("floor", self.floor, self._floor_name(), values)
        self._check_pin("ceiling", self.ceiling, self._ceiling_name(), values)

    def _relation(self) -> str:
        if self.down:
            if self.strict:
                return _("%(first)s must be greater than %(second)s, got %(left)s and %(right)s")
            return _("%(first)s must be greater than or equal to %(second)s, got %(left)s and %(right)s")
        if self.strict:
            return _("%(first)s must be less than %(second)s, got %(left)s and %(right)s")
        return _("%(first)s must be less than or equal to %(second)s, got %(left)s and %(right)s")

    def _ordered(self, left: float, right: float) -> bool:
        if self.down:
            return left > right if self.strict else left >= right
        return left < right if self.strict else left <= right

    def _check_pin(self, code: str, pinned: float | None, name: str, values: Mapping[str, Any]) -> None:
        if pinned is not None and values[name] != pinned:
            raise ConstraintError(
                code,
                _("%(name)s must be %(pinned)s, got %(actual)s")
                % {"name": name, "pinned": _number(pinned), "actual": _number(values[name])},
            )


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
