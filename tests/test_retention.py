import logging
from datetime import UTC, datetime
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from rest_framework.test import APIClient

from tests.test_services import apply
from tunables import Change
from tunables.models import ChangeItem, ChangeSet, Snapshot
from tunables.services import prune_snapshots
from tunables.sync import SyncResult

pytestmark = pytest.mark.django_db

BASE = "/api/tunables/"


@pytest.fixture
def history(synced: SyncResult) -> None:
    for rate in (0.20, 0.21, 0.22, 0.23, 0.24):
        apply(Change("pricing.vat_rate", rate))


def versions() -> list[int]:
    return sorted(Snapshot.objects.values_list("version", flat=True))


def run(*args: str) -> str:
    out = StringIO()
    call_command("tunables_prune_snapshots", *args, stdout=out)
    return out.getvalue()


def test_keep_retains_the_newest_and_the_protected_versions(history: None) -> None:
    assert versions() == [0, 1, 2, 3, 4, 5]
    assert prune_snapshots(keep=2) == [1, 2, 3]
    assert versions() == [0, 4, 5]


def test_keep_zero_leaves_only_the_protected_versions(history: None) -> None:
    assert prune_snapshots(keep=0) == [1, 2, 3, 4]
    assert versions() == [0, 5]


def test_before_retains_by_age(history: None) -> None:
    cutoff = datetime(2026, 6, 1, tzinfo=UTC)
    Snapshot._base_manager.filter(version__in=[1, 2]).update(created_at=datetime(2026, 5, 1, tzinfo=UTC))
    assert prune_snapshots(before=cutoff) == [1, 2]
    assert versions() == [0, 3, 4, 5]


def test_the_current_and_zero_versions_survive_every_policy(history: None) -> None:
    Snapshot._base_manager.all().update(created_at=datetime(2026, 5, 1, tzinfo=UTC))
    assert prune_snapshots(before=datetime(2030, 1, 1, tzinfo=UTC)) == [1, 2, 3, 4]
    assert versions() == [0, 5]


def test_history_rows_are_never_touched(history: None) -> None:
    prune_snapshots(keep=0)
    assert ChangeSet.objects.count() == 5
    assert ChangeItem.objects.count() == 5


def test_dry_run_reports_without_deleting(history: None) -> None:
    assert prune_snapshots(keep=2, dry_run=True) == [1, 2, 3]
    assert versions() == [0, 1, 2, 3, 4, 5]


def test_a_policy_is_required(history: None) -> None:
    with pytest.raises(ValueError, match="keep or before"):
        prune_snapshots()
    with pytest.raises(ValueError, match="keep or before"):
        prune_snapshots(keep=2, before=datetime(2026, 6, 1, tzinfo=UTC))


def test_pruning_twice_removes_nothing_more(history: None) -> None:
    prune_snapshots(keep=2)
    assert prune_snapshots(keep=2) == []
    assert versions() == [0, 4, 5]


def test_command_reports_what_it_removed(history: None) -> None:
    assert run("--keep", "2", "--dry-run") == "would remove 3 snapshots: versions 1 to 3\n"
    assert versions() == [0, 1, 2, 3, 4, 5]
    assert run("--keep", "2") == "removed 3 snapshots: versions 1 to 3\n"
    assert run("--keep", "2") == "no snapshots to remove\n"


def test_command_requires_one_policy(history: None) -> None:
    with pytest.raises(CommandError, match="--keep or --before"):
        run()
    with pytest.raises(CommandError, match="--keep or --before"):
        run("--keep", "2", "--before", "2026-06-01T00:00:00+00:00")


def test_command_rejects_a_bad_instant(history: None) -> None:
    with pytest.raises(CommandError, match="ISO 8601"):
        run("--before", "yesterday")
    with pytest.raises(CommandError, match="UTC offset"):
        run("--before", "2026-06-01T00:00:00")
    with pytest.raises(CommandError, match="ISO 8601"):
        run("--before", "2026-13-45T00:00:00+00:00")


def test_command_accepts_an_instant(history: None) -> None:
    Snapshot._base_manager.filter(version=1).update(created_at=datetime(2026, 5, 1, tzinfo=UTC))
    assert run("--before", "2026-06-01T00:00:00+00:00") == "removed 1 snapshot: version 1\n"
    assert versions() == [0, 2, 3, 4, 5]


def test_the_api_reports_a_pruned_version_as_missing(history: None) -> None:
    prune_snapshots(keep=2)
    api = APIClient()
    assert api.get(f"{BASE}snapshots/2/").status_code == 404
    assert api.get(f"{BASE}diff/?from=2&to=5").status_code == 404
    response = api.post(f"{BASE}rollback/", {"to_version": 2}, format="json")
    assert response.status_code == 422
    assert response.json()["type"] == "urn:tunables:problem:unknown-version"
    assert api.get(f"{BASE}snapshots/latest/").status_code == 200


def test_reading_and_writing_continue_after_pruning(history: None) -> None:
    prune_snapshots(keep=1)
    result = apply(Change("pricing.vat_rate", 0.25))
    assert result.version == 6
    assert versions() == [0, 5, 6]


def test_delete_rows_is_the_only_way_through_the_guard(history: None) -> None:
    from tunables.errors import HistoryIsAppendOnly

    with pytest.raises(HistoryIsAppendOnly):
        Snapshot.objects.filter(version=1).delete()
    assert Snapshot.objects.filter(version=1).delete_rows() == 1


def test_prune_needs_no_catalogue_state(db: None) -> None:
    assert prune_snapshots(keep=2) == []


@pytest.fixture
def long_history(synced: SyncResult) -> None:
    for rate in [0.10 + step / 100 for step in range(9)]:
        apply(Change("pricing.vat_rate", round(rate, 2)))


def test_deletion_runs_in_batches(long_history: None, django_assert_num_queries: Any) -> None:
    assert versions() == list(range(10))
    # One query lists the versions, then one delete per batch of three.
    with django_assert_num_queries(4):
        removed = prune_snapshots(keep=1, batch_size=3)
    assert removed == [1, 2, 3, 4, 5, 6, 7, 8]
    assert versions() == [0, 9]


def test_a_batch_larger_than_the_work_deletes_once(long_history: None, django_assert_num_queries: Any) -> None:
    with django_assert_num_queries(2):
        prune_snapshots(keep=1, batch_size=500)
    assert versions() == [0, 9]


def test_batch_size_must_be_positive(history: None) -> None:
    for size in (0, -1):
        with pytest.raises(ValueError, match="batch_size"):
            prune_snapshots(keep=1, batch_size=size)
    with pytest.raises(CommandError, match="--batch-size"):
        run("--keep", "1", "--batch-size", "0")


def test_a_prune_is_logged(history: None, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="tunables.services"):
        prune_snapshots(keep=2)
    assert len(caplog.records) == 1
    assert caplog.records[0].getMessage() == "pruned 3 snapshots, versions 1 to 3, policy keep=2"


def test_a_before_prune_names_its_policy(history: None, caplog: pytest.LogCaptureFixture) -> None:
    Snapshot._base_manager.filter(version=1).update(created_at=datetime(2026, 5, 1, tzinfo=UTC))
    with caplog.at_level(logging.INFO, logger="tunables.services"):
        prune_snapshots(before=datetime(2026, 6, 1, tzinfo=UTC))
    assert caplog.records[0].getMessage() == ("pruned 1 snapshot, version 1, policy before=2026-06-01 00:00:00+00:00")


def test_a_dry_run_logs_nothing(history: None, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="tunables.services"):
        assert prune_snapshots(keep=2, dry_run=True) == [1, 2, 3]
        assert prune_snapshots(keep=99) == []
    assert caplog.records == []


def test_the_command_passes_the_batch_size(long_history: None, django_assert_num_queries: Any) -> None:
    assert run("--keep", "1", "--batch-size", "4") == "removed 8 snapshots: versions 1 to 8\n"
    assert versions() == [0, 9]
