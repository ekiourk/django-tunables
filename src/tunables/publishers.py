import json
import logging
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.dispatch import receiver
from django.utils.module_loading import import_string

from tunables.conf import settings
from tunables.models import Snapshot
from tunables.signals import snapshot_published

logger = logging.getLogger(__name__)


class Publisher(Protocol):
    def publish(self, snapshot: Snapshot) -> None: ...


class FilePublisher:
    """Writes the snapshot document to TUNABLES["FILE_PUBLISHER_PATH"], atomically."""

    def __init__(self) -> None:
        if not settings.FILE_PUBLISHER_PATH:
            raise ImproperlyConfigured("TUNABLES['FILE_PUBLISHER_PATH'] is required by FilePublisher")
        self.path = Path(settings.FILE_PUBLISHER_PATH)

    def publish(self, snapshot: Snapshot) -> None:
        with tempfile.NamedTemporaryFile(
            "w", dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp", delete=False
        ) as handle:
            json.dump(snapshot.document, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        Path(handle.name).replace(self.path)


_publishers: list[Publisher] | None = None


def get_publishers() -> Sequence[Publisher]:
    """Publishers from TUNABLES["PUBLISHERS"], instantiated once."""
    global _publishers
    if _publishers is None:
        _publishers = [import_string(path)() for path in settings.PUBLISHERS]
    return _publishers


def reset() -> None:
    global _publishers
    _publishers = None


def publish(snapshot: Snapshot) -> None:
    """Send snapshot_published, then hand the snapshot to every publisher. Publisher errors are logged."""
    snapshot_published.send(sender=Snapshot, snapshot=snapshot)
    for publisher in get_publishers():
        try:
            publisher.publish(snapshot)
        except Exception:
            logger.exception("publisher %s failed for snapshot %s", type(publisher).__name__, snapshot.version)


@receiver(setting_changed)
def _on_setting_changed(*, setting: str, **_: Any) -> None:
    if setting == "TUNABLES":
        reset()
