import threading
import time
from typing import Any

import pytest
from django.test import override_settings

from tests.catalogue import catalogue
from tests.test_services import apply
from tunables import Change, reader, services, values
from tunables.errors import CatalogueError, UnknownKey
from tunables.models import State
from tunables.sync import SyncResult

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def fresh_cache() -> Any:
    values.invalidate()
    yield
    values.invalidate()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Any:
    now = [1000.0]
    monkeypatch.setattr(reader, "_now", lambda: now[0])
    return now


def test_reads_match_the_service_layer(synced: SyncResult) -> None:
    apply(Change("pricing.vat_rate", 0.2))
    values.invalidate()
    assert values.all() == services.current_values()
    assert values.get("pricing.vat_rate") == 0.2
    assert values.get("pricing.currencies") == ["EUR"]
    assert values.group("thermostat") == services.current_values()["thermostat"]
    assert values.version == 1


def test_values_are_coerced_python_objects(synced: SyncResult) -> None:
    apply(Change("thermostat.target_c", 19))
    values.invalidate()
    target = values.get("thermostat.target_c")
    assert isinstance(target, float)
    assert target == 19.0


def test_unknown_key_and_group_raise(synced: SyncResult) -> None:
    with pytest.raises(UnknownKey):
        values.get("pricing.nope")
    with pytest.raises(CatalogueError):
        values.group("nope")


def test_cache_serves_reads_without_queries(synced: SyncResult, django_assert_num_queries: Any, clock: Any) -> None:
    with django_assert_num_queries(2):
        values.all()
    with django_assert_num_queries(0):
        values.get("pricing.vat_rate")
        values.group("pricing")
        values.all()


def test_cache_checks_the_version_once_per_window(
    synced: SyncResult, django_assert_num_queries: Any, clock: Any
) -> None:
    values.all()
    clock[0] += 2.0
    with django_assert_num_queries(1):
        values.get("pricing.vat_rate")
    with django_assert_num_queries(0):
        values.get("pricing.vat_rate")


def test_a_version_moved_by_another_process_reloads(
    synced: SyncResult, django_assert_num_queries: Any, clock: Any
) -> None:
    values.all()
    stale = values._values
    apply(Change("pricing.vat_rate", 0.3))
    # Pretend this process never saw the write, the way a second process would not.
    values._values, values._version, values._checked_at = stale, 0, clock[0]
    clock[0] += 2.0
    with django_assert_num_queries(2):
        assert values.get("pricing.vat_rate") == 0.3
    assert values.version == 1


@override_settings(TUNABLES={"CATALOGUE": "tests.catalogue.catalogue", "READ_CACHE_TTL": 0})
def test_zero_ttl_checks_every_read(synced: SyncResult, django_assert_num_queries: Any, clock: Any) -> None:
    values.all()
    with django_assert_num_queries(1):
        values.get("pricing.vat_rate")
    with django_assert_num_queries(1):
        values.get("pricing.vat_rate")


@override_settings(TUNABLES={"CATALOGUE": "tests.catalogue.catalogue", "READ_CACHE_TTL": None})
def test_no_ttl_never_checks(synced: SyncResult, django_assert_num_queries: Any, clock: Any) -> None:
    values.all()
    State.objects.filter(pk=1).update(current_version=7)
    clock[0] += 3600.0
    with django_assert_num_queries(0):
        assert values.version == 0


def test_a_write_in_this_process_invalidates(synced: SyncResult, django_assert_num_queries: Any, clock: Any) -> None:
    assert values.get("pricing.vat_rate") == 0.24
    apply(Change("pricing.vat_rate", 0.19))
    with django_assert_num_queries(2):
        assert values.get("pricing.vat_rate") == 0.19
    assert values.version == 1


def test_defaults_before_the_first_sync(db: None) -> None:
    assert not State.objects.exists()
    assert values.all() == catalogue.defaults()
    assert values.version == 0
    assert values.get("pricing.vat_rate") == 0.24


def test_overrides_are_served_while_out_of_sync(synced: SyncResult) -> None:
    apply(Change("pricing.vat_rate", 0.2))
    State.objects.filter(pk=1).update(catalogue_version="sha256:stale")
    values.invalidate()
    assert values.get("pricing.vat_rate") == 0.2


def test_changing_the_setting_resets_the_cache(synced: SyncResult, django_assert_num_queries: Any) -> None:
    assert values.get("pricing.vat_rate") == 0.24
    override = override_settings(TUNABLES={"CATALOGUE": "tests.catalogue.catalogue", "READ_CACHE_TTL": None})
    with override, django_assert_num_queries(2):
        assert values.version == 0


def test_concurrent_reads_load_once(monkeypatch: pytest.MonkeyPatch, clock: Any) -> None:
    loads: list[int] = []

    def slow_load(self: Any, version: int) -> None:
        loads.append(version)
        time.sleep(0.05)
        self._values = {"pricing": {"vat_rate": 0.21}}
        self._version = version

    monkeypatch.setattr(reader, "_stored_version", lambda: 0)
    monkeypatch.setattr(reader.Values, "_load", slow_load)
    values.invalidate()
    seen: list[Any] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(4)

    def read() -> None:
        try:
            barrier.wait()
            seen.append(values.get("pricing.vat_rate"))
        except BaseException as error:  # pragma: no cover - reported below
            errors.append(error)

    threads = [threading.Thread(target=read) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert seen == [0.21, 0.21, 0.21, 0.21]
    assert loads == [0]
