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
