import json
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import override_settings

from tests import test_sync
from tests.test_services import apply
from tunables import Change
from tunables.models import ChangeSet, Snapshot, State
from tunables.sync import SyncResult

pytestmark = pytest.mark.django_db

EXTENDED = override_settings(TUNABLES={"CATALOGUE": f"{test_sync.__name__}.extended"})


def run(*args: Any, **options: Any) -> str:
    out = StringIO()
    call_command(*args, stdout=out, **options)
    return out.getvalue()


def latest_document() -> dict[str, Any]:
    return dict(Snapshot.objects.order_by("-version").first().document)  # type: ignore[union-attr]


def test_sync_command_reports_what_it_did() -> None:
    assert run("tunables_sync") == "created state and snapshot 0\n"
    assert State.objects.get().current_version == 0
    assert run("tunables_sync") == "in sync at version 0\n"
    with EXTENDED:
        assert run("tunables_sync") == "catalogue changed, wrote version 1\n"
    assert State.objects.get().current_version == 1


def test_sync_check() -> None:
    with pytest.raises(CommandError) as info:
        run("tunables_sync", "--check")
    assert info.value.returncode == 1
    assert not State.objects.exists()
    run("tunables_sync")
    assert run("tunables_sync", "--check") == "in sync at version 0\n"
    with EXTENDED, pytest.raises(CommandError) as info:
        run("tunables_sync", "--check")
    assert info.value.returncode == 1
    assert State.objects.get().current_version == 0


def test_show_text(synced: SyncResult) -> None:
    apply(Change("pricing.vat_rate", 0.2), Change("thermostat.mode", "heat"))
    lines = run("tunables_show").splitlines()
    assert lines[0] == "version 1"
    assert lines[1:] == [
        "pricing.vat_rate = 0.2  (override)",
        "pricing.free_shipping_over = 50.0",
        'pricing.currencies = ["EUR"]',
        "pricing.allow_backorders = false",
        "thermostat.target_c = 21.0",
        'thermostat.mode = "heat"  (override)',
        "thermostat.sample_interval = 60.0",
        'thermostat.display_colour = "#ffffff"',
        "thermostat.legacy_offset = 0.0",
        "weights.alpha = 0.5",
        "weights.beta = 0.3",
        "weights.gamma = 0.2",
    ]


def test_show_group_filter(synced: SyncResult) -> None:
    lines = run("tunables_show", "--group", "weights").splitlines()
    assert lines == ["version 0", "weights.alpha = 0.5", "weights.beta = 0.3", "weights.gamma = 0.2"]
    with pytest.raises(CommandError, match="unknown group 'shop'"):
        run("tunables_show", "--group", "shop")


def test_show_json(synced: SyncResult) -> None:
    apply(Change("pricing.vat_rate", 0.2))
    assert json.loads(run("tunables_show", "--json")) == latest_document()


def test_show_and_export_require_sync() -> None:
    with pytest.raises(CommandError, match="tunables_sync"):
        run("tunables_show")
    with pytest.raises(CommandError, match="tunables_sync"):
        run("tunables_export")


def test_export(synced: SyncResult, tmp_path: Path) -> None:
    apply(Change("pricing.vat_rate", 0.2))
    target = tmp_path / "out.json"
    assert run("tunables_export", "--output", str(target)) == f"wrote {target}\n"
    assert json.loads(target.read_text()) == latest_document()
    assert target.read_text().endswith("}\n")
    assert json.loads(run("tunables_export")) == latest_document()


def write_document(tmp_path: Path, **changes: Any) -> Path:
    document = latest_document()
    for key, value in changes.items():
        group, _, name = key.partition("__")
        document["groups"].setdefault(group, {})[name] = value
    path = tmp_path / "import.json"
    path.write_text(json.dumps(document))
    return path


def test_import_lenient(synced: SyncResult, tmp_path: Path) -> None:
    path = write_document(tmp_path, pricing__vat_rate=0.2, pricing__discount=5, shop__open=True)
    err = StringIO()
    out = run("tunables_import", str(path), "--actor", "deploy", "--reason", "release 4", stderr=err)
    assert out == "wrote version 1\n"
    assert err.getvalue().splitlines() == [
        "skipped pricing.discount: unknown tunable 'pricing.discount'",
        "skipped shop.open: unknown tunable 'shop.open'",
    ]
    changeset = ChangeSet.objects.get(version=1)
    assert (changeset.actor, changeset.actor_source, changeset.source) == ("deploy", "system", "import")
    assert changeset.reason == "release 4"
    assert [item.key for item in changeset.items.all()] == ["pricing.vat_rate"]
    assert latest_document()["groups"]["pricing"]["vat_rate"] == 0.2


def test_import_strict(synced: SyncResult, tmp_path: Path) -> None:
    path = write_document(tmp_path, pricing__vat_rate=0.2, pricing__discount=5)
    with pytest.raises(CommandError, match="pricing.discount: unknown_key"):
        run("tunables_import", str(path), "--actor", "deploy", "--strict")
    assert ChangeSet.objects.count() == 0


def test_import_identical_document(synced: SyncResult, tmp_path: Path) -> None:
    path = write_document(tmp_path)
    assert run("tunables_import", str(path), "--actor", "deploy") == "nothing to change\n"
    assert ChangeSet.objects.count() == 0


def test_import_rejects_bad_documents(synced: SyncResult, tmp_path: Path) -> None:
    path = write_document(tmp_path, pricing__vat_rate=7.0, thermostat__mode="eco")
    with pytest.raises(CommandError) as info:
        run("tunables_import", str(path), "--actor", "deploy")
    assert str(info.value).splitlines() == [
        "pricing.vat_rate: max: must be <= 1.0",
        "thermostat.mode: enum: must be one of: auto, heat, cool, off",
    ]
    document = latest_document()
    document["format_version"] = 2
    path.write_text(json.dumps(document))
    with pytest.raises(CommandError, match="format_version"):
        run("tunables_import", str(path), "--actor", "deploy")
    path.write_text("{not json")
    with pytest.raises(CommandError, match="not valid JSON"):
        run("tunables_import", str(path), "--actor", "deploy")
    assert ChangeSet.objects.count() == 0


def test_protect_history_requires_postgresql(db: None) -> None:
    if connection.vendor == "postgresql":
        pytest.skip("runs on SQLite only")
    with pytest.raises(CommandError, match="needs PostgreSQL.*sqlite"):
        run("tunables_protect_history")
