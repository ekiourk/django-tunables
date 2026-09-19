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
from tunables.models import PublisherState, Snapshot
from tunables.publishers import FilePublisher, QueuedPublisher
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
    raise_error = True

    def publish(self, snapshot: Snapshot) -> None:
        if Failing.raise_error:
            raise RuntimeError("boom")


class Queued(QueuedPublisher):
    versions: list[int] = []

    def enqueue(self, version: int) -> None:
        Queued.versions.append(version)


class QueueDown(QueuedPublisher):
    def enqueue(self, version: int) -> None:
        raise RuntimeError("queue unreachable")


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


def test_queued_publisher_passes_the_version(
    synced: SyncResult, django_capture_on_commit_callbacks: Callable[..., Any]
) -> None:
    Queued.versions = []
    with (
        with_publishers(f"{__name__}.Queued"),
        django_capture_on_commit_callbacks(execute=True),
    ):
        result = apply(Change("pricing.vat_rate", 0.2))
    assert Queued.versions == [result.version]


def test_queued_publisher_needs_enqueue(synced: SyncResult) -> None:
    with pytest.raises(NotImplementedError):
        QueuedPublisher().publish(Snapshot.objects.get(version=0))


def test_a_failing_queue_is_recorded_like_any_publisher(
    synced: SyncResult, django_capture_on_commit_callbacks: Callable[..., Any], caplog: pytest.LogCaptureFixture
) -> None:
    with (
        with_publishers(f"{__name__}.QueueDown"),
        django_capture_on_commit_callbacks(execute=True),
        caplog.at_level(logging.ERROR, logger="tunables"),
    ):
        apply(Change("pricing.vat_rate", 0.2))
    state = PublisherState.objects.get(publisher=f"{__name__}.QueueDown")
    assert state.last_error == "queue unreachable"
    assert state.last_version is None


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


RECORDER = f"{__name__}.Recorder"
FAILING = f"{__name__}.Failing"


def states() -> dict[str, tuple[int | None, str]]:
    from tunables.models import PublisherState

    return {row.publisher: (row.last_version, row.last_error) for row in PublisherState.objects.all()}


def test_publish_records_the_version_per_publisher(
    synced: SyncResult, django_capture_on_commit_callbacks: Callable[..., Any]
) -> None:
    from tunables.models import PublisherState

    with with_publishers(RECORDER, FAILING), django_capture_on_commit_callbacks(execute=True):
        apply(Change("pricing.vat_rate", 0.2))
    assert states() == {RECORDER: (1, ""), FAILING: (None, "boom")}
    assert PublisherState.objects.get(publisher=RECORDER).last_published_at is not None
    assert PublisherState.objects.get(publisher=FAILING).last_published_at is None


def test_a_later_success_clears_the_error(
    synced: SyncResult, django_capture_on_commit_callbacks: Callable[..., Any]
) -> None:
    with with_publishers(FAILING), django_capture_on_commit_callbacks(execute=True):
        apply(Change("pricing.vat_rate", 0.2))
    assert states() == {FAILING: (None, "boom")}
    Failing.raise_error = False
    try:
        with with_publishers(FAILING), django_capture_on_commit_callbacks(execute=True):
            apply(Change("pricing.vat_rate", 0.1))
    finally:
        Failing.raise_error = True
    assert states() == {FAILING: (2, "")}


def test_republish_latest_and_given_version(synced: SyncResult) -> None:
    first = apply(Change("pricing.vat_rate", 0.2))
    second = apply(Change("pricing.vat_rate", 0.1))
    with with_publishers(RECORDER, FAILING):
        results = publishers.republish()
    assert results == [
        publishers.PublishResult(RECORDER, 2),
        publishers.PublishResult(FAILING, 2, "boom"),
    ]
    assert Recorder.published == [second.snapshot]
    assert states() == {RECORDER: (2, ""), FAILING: (None, "boom")}
    with with_publishers(RECORDER, FAILING):
        results = publishers.republish(version=1, publisher=RECORDER)
    assert results == [publishers.PublishResult(RECORDER, 1)]
    assert Recorder.published == [second.snapshot, first.snapshot]
    assert states()[RECORDER] == (1, "")


def test_republish_rejects_unknown_version_and_publisher(synced: SyncResult) -> None:
    from tunables.errors import UnknownVersion

    with with_publishers(RECORDER):
        with pytest.raises(UnknownVersion):
            publishers.republish(version=9)
        with pytest.raises(ValueError, match="not configured"):
            publishers.republish(publisher="tests.nowhere.Missing")
