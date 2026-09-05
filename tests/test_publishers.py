import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from tests.test_services import apply
from tunables import Change, publishers
from tunables.errors import ValidationFailed
from tunables.models import Snapshot
from tunables.publishers import FilePublisher
from tunables.signals import snapshot_published
from tunables.sync import SyncResult, sync

pytestmark = pytest.mark.django_db

CATALOGUE = "tests.catalogue.catalogue"


class Recorder:
    instances = 0
    published: list[Snapshot] = []
    events: list[str] = []

    def __init__(self) -> None:
        Recorder.instances += 1

    def publish(self, snapshot: Snapshot) -> None:
        Recorder.published.append(snapshot)
        Recorder.events.append("publisher")


class Failing:
    def publish(self, snapshot: Snapshot) -> None:
        raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def clean_recorder() -> None:
    Recorder.instances = 0
    Recorder.published = []
    Recorder.events = []
    publishers.reset()


def with_publishers(*paths: str, **extra: Any) -> Any:
    return override_settings(TUNABLES={"CATALOGUE": CATALOGUE, "PUBLISHERS": list(paths), **extra})


def test_signal_fires_after_commit(synced: SyncResult, django_capture_on_commit_callbacks: Callable[..., Any]) -> None:
    received: list[tuple[Any, Snapshot]] = []

    def receiver(sender: Any, snapshot: Snapshot, **kwargs: Any) -> None:
        received.append((sender, snapshot))

    snapshot_published.connect(receiver)
    try:
        with django_capture_on_commit_callbacks(execute=True):
            result = apply(Change("pricing.vat_rate", 0.2))
        assert received == [(Snapshot, result.snapshot)]
        with django_capture_on_commit_callbacks(execute=True), pytest.raises(ValidationFailed):
            apply(Change("pricing.vat_rate", 5.0))
        assert len(received) == 1
    finally:
        snapshot_published.disconnect(receiver)


def test_configured_publisher_is_instantiated_once_and_called_after_each_commit(
    synced: SyncResult, django_capture_on_commit_callbacks: Callable[..., Any]
) -> None:
    def receiver(sender: Any, snapshot: Snapshot, **kwargs: Any) -> None:
        Recorder.events.append("signal")

    snapshot_published.connect(receiver)
    try:
        with with_publishers(f"{__name__}.Recorder"), django_capture_on_commit_callbacks(execute=True):
            first = apply(Change("pricing.vat_rate", 0.2))
            second = apply(Change("pricing.vat_rate", 0.1))
    finally:
        snapshot_published.disconnect(receiver)
    assert Recorder.published == [first.snapshot, second.snapshot]
    assert Recorder.instances == 1
    assert Recorder.events == ["signal", "publisher", "signal", "publisher"]


def test_publisher_failure_is_logged_not_raised(
    synced: SyncResult, django_capture_on_commit_callbacks: Callable[..., Any], caplog: pytest.LogCaptureFixture
) -> None:
    with (
        with_publishers(f"{__name__}.Failing", f"{__name__}.Recorder"),
        django_capture_on_commit_callbacks(execute=True),
        caplog.at_level(logging.ERROR, logger="tunables"),
    ):
        result = apply(Change("pricing.vat_rate", 0.2))
    assert result.version == 1
    assert Recorder.published == [result.snapshot]
    assert len(caplog.records) == 1
    assert caplog.records[0].name.startswith("tunables")
    assert "Failing" in caplog.records[0].getMessage()
    assert caplog.records[0].exc_info is not None


def test_bad_publisher_path_is_a_configuration_error(
    synced: SyncResult, django_capture_on_commit_callbacks: Callable[..., Any]
) -> None:
    with (
        with_publishers("tests.nowhere.Missing"),
        pytest.raises(ImportError),
        django_capture_on_commit_callbacks(execute=True),
    ):
        apply(Change("pricing.vat_rate", 0.2))


def test_file_publisher_writes_document_atomically(
    synced: SyncResult, django_capture_on_commit_callbacks: Callable[..., Any], tmp_path: Path
) -> None:
    target = tmp_path / "tunables.json"
    with (
        with_publishers("tunables.publishers.FilePublisher", FILE_PUBLISHER_PATH=str(target)),
        django_capture_on_commit_callbacks(execute=True),
    ):
        result = apply(Change("pricing.vat_rate", 0.2))
    assert json.loads(target.read_text()) == result.snapshot.document
    assert [p.name for p in tmp_path.iterdir()] == ["tunables.json"]


def test_file_publisher_requires_a_path() -> None:
    with pytest.raises(ImproperlyConfigured, match="FILE_PUBLISHER_PATH"):
        FilePublisher()


def test_sync_publishes_snapshot_zero(db: None, django_capture_on_commit_callbacks: Callable[..., Any]) -> None:
    with with_publishers(f"{__name__}.Recorder"), django_capture_on_commit_callbacks(execute=True):
        sync()
    assert [snapshot.version for snapshot in Recorder.published] == [0]


def test_get_publishers_follows_settings() -> None:
    assert publishers.get_publishers() == []
    with with_publishers(f"{__name__}.Recorder"):
        assert [type(p) for p in publishers.get_publishers()] == [Recorder]
        assert publishers.get_publishers() is publishers.get_publishers()
    assert publishers.get_publishers() == []
