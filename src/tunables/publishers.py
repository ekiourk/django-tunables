from collections.abc import Sequence
from typing import Protocol

from tunables.models import Snapshot


class Publisher(Protocol):
    def publish(self, snapshot: Snapshot) -> None: ...


class FilePublisher:
    """Writes the snapshot document to TUNABLES["FILE_PUBLISHER_PATH"], atomically."""

    def __init__(self) -> None:
        raise NotImplementedError

    def publish(self, snapshot: Snapshot) -> None:
        raise NotImplementedError


def get_publishers() -> Sequence[Publisher]:
    """Publishers from TUNABLES["PUBLISHERS"], instantiated once."""
    raise NotImplementedError


def reset() -> None:
    raise NotImplementedError
