"""In-process reader for the effective values, cached in memory."""

import threading
import time
from typing import Any

from tunables.conf import settings
from tunables.errors import CatalogueError
from tunables.registry import get_catalogue


def _now() -> float:
    return time.monotonic()


def _stored_version() -> int:
    from tunables.models import State

    stored = State.objects.filter(pk=1).values_list("current_version", flat=True).first()
    return 0 if stored is None else int(stored)


class Values:
    """Effective values of the running catalogue, cached per process.

    Reads serve from memory. At most once per ``TUNABLES["READ_CACHE_TTL"]`` seconds a read
    checks the stored version and reloads when it moved. A write in this process drops the
    cache at once, so it never serves a value it just replaced.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, dict[str, Any]] | None = None
        self._version = 0
        self._checked_at = 0.0

    def get(self, key: str) -> Any:
        """The effective value of one full key, coerced. Raises UnknownKey for a key not in the catalogue."""
        get_catalogue().get(key)
        group, _, name = key.partition(".")
        return self._current()[group][name]

    def group(self, name: str) -> dict[str, Any]:
        """The effective values of one group, {name: value}. Raises CatalogueError for an unknown group."""
        if name not in get_catalogue().groups:
            raise CatalogueError(f"unknown group {name!r}")
        return dict(self._current()[name])

    def all(self) -> dict[str, dict[str, Any]]:
        """Every effective value, {group: {name: value}}."""
        return {group: dict(values) for group, values in self._current().items()}

    @property
    def version(self) -> int:
        """The version the cached values came from, 0 before the first sync."""
        self._current()
        return self._version

    def invalidate(self) -> None:
        """Drop the cache so the next read reloads."""
        with self._lock:
            self._values = None

    def _current(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            if self._values is not None and self._within_window():
                return self._values
            self._checked_at = _now()
            version = _stored_version()
            if self._values is None or version != self._version:
                self._load(version)
            assert self._values is not None
            return self._values

    def _within_window(self) -> bool:
        ttl = settings.READ_CACHE_TTL
        return ttl is None or _now() - self._checked_at < ttl

    def _load(self, version: int) -> None:
        from tunables.services import current_values

        self._values = current_values()
        self._version = version


values = Values()


def _invalidate_on_publish(**_: Any) -> None:
    values.invalidate()


def _invalidate_on_setting_changed(*, setting: str, **_: Any) -> None:
    if setting == "TUNABLES":
        values.invalidate()
