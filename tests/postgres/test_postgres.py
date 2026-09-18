import threading
import time
from io import StringIO
from typing import Any
from unittest import mock

import pytest
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction

from tests.test_services import apply
from tunables import Change, services
from tunables.errors import VersionConflict
from tunables.models import ChangeItem, ChangeSet, Snapshot, State, TunableDefinition
from tunables.sync import SyncResult

pytestmark = [pytest.mark.postgres, pytest.mark.django_db]

HISTORY = {ChangeSet: "reason = 'edited'", ChangeItem: "reset = NOT reset", Snapshot: "format_version = 9"}
TABLES = [model._meta.db_table for model in HISTORY]
ALL_TABLES = [*TABLES, "tunables_tunablevalue"]


def execute(sql: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(sql)


def protect(*args: str) -> str:
    out = StringIO()
    call_command("tunables_protect_history", *args, stdout=out)
    return out.getvalue()


def counts() -> list[int]:
    return [model.objects.count() for model in HISTORY]


def test_runs_on_postgresql_with_all_tables() -> None:
    assert connection.vendor == "postgresql"
    tables = set(connection.introspection.table_names())
    assert {"tunables_state", "tunables_tunabledefinition", *ALL_TABLES} <= tables


def test_protect_history_rejects_update_delete_and_truncate(synced: SyncResult) -> None:
    apply(Change("pricing.vat_rate", 0.2))
    assert protect() == "installed history protection\n"
    assert protect() == "installed history protection\n"
    before = counts()
    for model, assignment in HISTORY.items():
        table = model._meta.db_table
        for sql in (f"UPDATE {table} SET {assignment}", f"DELETE FROM {table}"):
            with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
                execute(sql)
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        # Deferred FK checks from this test's own inserts would block TRUNCATE before the trigger runs.
        execute("SET CONSTRAINTS ALL IMMEDIATE")
        execute(f"TRUNCATE {', '.join(ALL_TABLES)}")
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        ChangeSet._base_manager.all().update(reason="edited")
    assert counts() == before


def test_protect_history_remove(synced: SyncResult) -> None:
    apply(Change("pricing.vat_rate", 0.2))
    protect()
    assert protect("--remove") == "removed history protection\n"
    assert protect("--remove") == "removed history protection\n"
    execute("UPDATE tunables_changeset SET reason = 'edited'")
    assert ChangeSet.objects.get(version=1).reason == "edited"


def run_concurrently(**kwargs: Any) -> dict[str, Any]:
    entered = threading.Event()
    second_started = threading.Event()
    results: dict[str, Any] = {}
    original = services._prepare

    def slow_prepare(*args: Any) -> Any:
        if not entered.is_set():
            entered.set()
            second_started.wait(timeout=5)
            time.sleep(0.3)
        return original(*args)

    def worker(name: str, value: float) -> None:
        try:
            if name == "second":
                second_started.set()
            results[name] = apply(Change("pricing.vat_rate", value), **kwargs).version
        except VersionConflict as error:
            results[name] = error
        finally:
            connection.close()

    with mock.patch.object(services, "_prepare", slow_prepare):
        first = threading.Thread(target=worker, args=("first", 0.1))
        first.start()
        assert entered.wait(timeout=5)
        second = threading.Thread(target=worker, args=("second", 0.2))
        second.start()
        first.join(timeout=10)
        second.join(timeout=10)
    return results


@pytest.mark.django_db(transaction=True)
def test_concurrent_writers_with_expected_version_one_wins(synced: SyncResult) -> None:
    results = run_concurrently(expected_version=0)
    assert results["first"] == 1
    assert isinstance(results["second"], VersionConflict)
    assert (results["second"].expected, results["second"].actual) == (0, 1)
    assert ChangeSet.objects.count() == 1
    assert services.current_values()["pricing"]["vat_rate"] == 0.1


@pytest.mark.django_db(transaction=True)
def test_concurrent_writers_without_expected_version_both_win(synced: SyncResult) -> None:
    results = run_concurrently()
    assert sorted(results.values()) == [1, 2]
    assert ChangeSet.objects.count() == 2
    assert services.current_values()["pricing"]["vat_rate"] == 0.2


@pytest.mark.django_db(transaction=True)
def test_concurrent_syncs_on_a_fresh_database() -> None:
    from tunables import sync as sync_module

    entered = threading.Event()
    second_started = threading.Event()
    results: dict[str, Any] = {}
    original = sync_module._mirror

    def slow_mirror(*args: Any) -> None:
        if not entered.is_set():
            entered.set()
            second_started.wait(timeout=5)
            time.sleep(0.3)
        original(*args)

    def worker(name: str) -> None:
        try:
            if name == "second":
                second_started.set()
            results[name] = sync_module.sync()
        except Exception as error:  # noqa: BLE001
            results[name] = error
        finally:
            connection.close()

    with mock.patch.object(sync_module, "_mirror", slow_mirror):
        first = threading.Thread(target=worker, args=("first",))
        first.start()
        assert entered.wait(timeout=5)
        second = threading.Thread(target=worker, args=("second",))
        second.start()
        first.join(timeout=15)
        second.join(timeout=15)
    assert all(isinstance(r, SyncResult) for r in results.values()), results
    assert sorted(r.created for r in results.values()) == [False, True]
    assert State.objects.count() == 1
    assert Snapshot.objects.count() == 1
    assert ChangeSet.objects.count() == 0
    assert TunableDefinition.objects.count() == 14
