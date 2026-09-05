from typing import Any


def current_version() -> int:
    """Version in the State row. Raises CatalogueOutOfSync when the row is missing."""
    raise NotImplementedError


def current_values() -> dict[str, dict[str, Any]]:
    """Effective Python values, defaults overlaid with stored overrides: {group: {name: value}}."""
    raise NotImplementedError
