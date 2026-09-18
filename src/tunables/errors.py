from collections.abc import Sequence
from dataclasses import dataclass


class TunablesError(Exception):
    pass


class ConstraintError(TunablesError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TypeCoercionError(ConstraintError):
    def __init__(self, message: str) -> None:
        super().__init__("type", message)


class CatalogueError(TunablesError):
    pass


class UnknownKey(TunablesError):
    def __init__(self, key: str) -> None:
        super().__init__(f"unknown tunable {key!r}")
        self.key = key


class HistoryIsAppendOnly(TunablesError):
    pass


class CatalogueOutOfSync(TunablesError):
    pass


@dataclass(frozen=True)
class FieldError:
    key: str
    code: str
    message: str


@dataclass(frozen=True)
class GroupError:
    group: str
    code: str
    message: str


@dataclass(frozen=True)
class FieldWarning:
    key: str
    code: str
    message: str


@dataclass(frozen=True)
class CatalogueValidationError:
    """A catalogue-level validator rejected the proposed state; reported with scope "catalogue"."""

    code: str
    message: str


class ValidationFailed(TunablesError):
    def __init__(self, errors: Sequence[FieldError | GroupError | CatalogueValidationError]) -> None:
        super().__init__(f"{len(errors)} error{'s' if len(errors) != 1 else ''}")
        self.errors = list(errors)


class VersionConflict(TunablesError):
    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(f"expected version {expected}, current version is {actual}")
        self.expected = expected
        self.actual = actual


class NothingToChange(TunablesError):
    pass


class UnknownVersion(TunablesError):
    def __init__(self, version: int) -> None:
        super().__init__(f"no snapshot for version {version}")
        self.version = version


class GroupNotEditable(TunablesError):
    def __init__(self, group: str) -> None:
        super().__init__(f"group {group!r} is not editable by this request")
        self.group = group
