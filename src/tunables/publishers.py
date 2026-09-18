import json
import logging
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.dispatch import receiver
from django.utils import timezone
from django.utils.module_loading import import_string

from tunables.conf import settings
from tunables.errors import UnknownVersion
from tunables.models import PublisherState, Snapshot
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


@dataclass(frozen=True)
class PublishResult:
    publisher: str
    version: int
    error: str = ""


def republish(version: int | None = None, publisher: str | None = None) -> list[PublishResult]:
    """Send a stored snapshot, the latest by default, to every configured publisher or to one of them."""
    from tunables.services import latest_snapshot

    if version is None:
        snapshot = latest_snapshot()
    else:
        found = Snapshot.objects.filter(version=version).first()
        if found is None:
            raise UnknownVersion(version)
        snapshot = found
    configured = list(settings.PUBLISHERS)
    if publisher is not None and publisher not in configured:
        raise ValueError(f"publisher {publisher!r} is not configured in TUNABLES['PUBLISHERS']")
    targets = [(path, instance) for path, instance in zip(configured, get_publishers(), strict=True)]
    if publisher is not None:
        targets = [target for target in targets if target[0] == publisher]
    return [_send(path, instance, snapshot) for path, instance in targets]


def publish(snapshot: Snapshot) -> None:
    """Send snapshot_published, then hand the snapshot to every publisher. Publisher errors are logged."""
    snapshot_published.send(sender=Snapshot, snapshot=snapshot)
    for path, instance in zip(settings.PUBLISHERS, get_publishers(), strict=True):
        _send(path, instance, snapshot)


def _send(path: str, publisher: Publisher, snapshot: Snapshot) -> PublishResult:
    """Call one publisher and record the outcome in PublisherState."""
    state, _ = PublisherState.objects.get_or_create(publisher=path)
    try:
        publisher.publish(snapshot)
    except Exception as error:
        logger.exception("publisher %s failed for snapshot %s", path, snapshot.version)
        state.last_error = str(error) or type(error).__name__
        state.save(update_fields=["last_error", "updated_at"])
        return PublishResult(path, snapshot.version, state.last_error)
    state.last_version = snapshot.version
    state.last_published_at = timezone.now()
    state.last_error = ""
    state.save(update_fields=["last_version", "last_published_at", "last_error", "updated_at"])
    return PublishResult(path, snapshot.version)


@receiver(setting_changed)
def _on_setting_changed(*, setting: str, **_: Any) -> None:
    if setting == "TUNABLES":
        reset()
