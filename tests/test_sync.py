from dataclasses import replace
from typing import Any

import pytest
from django.test import override_settings

from tests.catalogue import catalogue, pricing, thermostat, weights
from tunables import Actor, Catalogue, Change, Float, Tunable, services
from tunables.document import FORMAT_VERSION, build_document
from tunables.models import (
    ChangeItem,
    ChangeSet,
    Snapshot,
    State,
    TunableDefinition,
    TunableValue,
)
from tunables.sync import SyncResult, is_synced, sync

pytestmark = pytest.mark.django_db

reworded = Catalogue([replace(pricing, title="Prices"), thermostat, weights])
extended = Catalogue(
    [
        replace(pricing, tunables=[*pricing.tunables, Tunable("discount", Float(min=0.0, max=1.0), 0.0)]),
        thermostat,
        weights,
    ]
)
reduced = Catalogue([replace(pricing, tunables=pricing.tunables[:-1]), thermostat, weights])


def use(alternative: str) -> Any:
    return override_settings(TUNABLES={"CATALOGUE": f"{__name__}.{alternative}"})


def override(key: str, value: Any) -> ChangeSet:
    return services.apply_changeset([Change(key, value)], actor=Actor("alice", "verified"), source="api").changeset


def test_fresh_database_bootstraps_state_and_snapshot_zero() -> None:
    result = sync()
    assert result == SyncResult(created=True, rebuilt=False, version=0)
    state = State.objects.get()
    assert state.pk == 1
    assert state.current_version == 0
    assert state.catalogue_version == catalogue.version
    snapshot = Snapshot.objects.get()
    assert snapshot.version == 0
    assert snapshot.changeset is None
    assert snapshot.format_version == FORMAT_VERSION
    assert snapshot.catalogue_version == catalogue.version
    assert snapshot.document == build_document(catalogue, {}, version=0, created_at=snapshot.created_at)
    assert ChangeSet.objects.count() == 0


def test_mirror_rows() -> None:
    sync()
    assert TunableDefinition.objects.count() == 12
    assert TunableDefinition.objects.filter(is_active=True).count() == 12
    vat = TunableDefinition.objects.get(key="pricing.vat_rate")
    assert (vat.group_name, vat.name, vat.order) == ("pricing", "vat_rate", 0)
    assert (vat.type_name, vat.type_params) == ("float", {"min": 0.0, "max": 1.0})
    assert vat.default == 0.24
    assert (vat.title, vat.description, vat.unit) == ("VAT rate", "", "")
    assert (vat.ui, vat.metadata, vat.deprecated) == ({}, {}, "")
    legacy = TunableDefinition.objects.get(key="thermostat.legacy_offset")
    assert (legacy.order, legacy.deprecated) == (4, "Use target_c instead.")
    assert TunableDefinition.objects.get(key="pricing.free_shipping_over").unit == "EUR"
    colour = TunableDefinition.objects.get(key="thermostat.display_colour")
    assert (colour.type_name, colour.type_params, colour.default) == ("hex_colour", {}, "#ffffff")


def test_second_sync_is_a_noop() -> None:
    sync()
    assert sync() == SyncResult(created=False, rebuilt=False, version=0)
    assert Snapshot.objects.count() == 1
    assert ChangeSet.objects.count() == 0
    assert TunableDefinition.objects.count() == 12


def test_wording_change_updates_mirror_without_new_version() -> None:
    sync()
    with use("reworded"):
        assert sync() == SyncResult(created=False, rebuilt=False, version=0)
    assert Snapshot.objects.count() == 1
    assert State.objects.get().catalogue_version == catalogue.version == reworded.version


def test_added_tunable_writes_a_system_version() -> None:
    sync()
    with use("extended"):
        assert sync() == SyncResult(created=False, rebuilt=True, version=1)
    changeset = ChangeSet.objects.get()
    assert changeset.version == 1
    assert (changeset.actor, changeset.actor_source, changeset.source) == ("system", "system", "system")
    assert changeset.reason == "catalogue changed"
    assert changeset.catalogue_version == extended.version
    assert changeset.items.count() == 0
    snapshot = Snapshot.objects.get(version=1)
    assert snapshot.changeset == changeset
    assert snapshot.document["groups"]["pricing"]["discount"] == 0.0
    assert snapshot.catalogue_version == extended.version
    state = State.objects.get()
    assert (state.current_version, state.catalogue_version) == (1, extended.version)
    assert TunableDefinition.objects.count() == 13


def test_removed_tunable_deactivates_and_drops_its_override() -> None:
    sync()
    override("pricing.allow_backorders", True)
    override("pricing.vat_rate", 0.2)
    with use("reduced"):
        assert sync() == SyncResult(created=False, rebuilt=True, version=3)
    definition = TunableDefinition.objects.get(key="pricing.allow_backorders")
    assert definition.is_active is False
    assert not TunableValue.objects.filter(key="pricing.allow_backorders").exists()
    assert TunableValue.objects.get(key="pricing.vat_rate").value == 0.2
    item = ChangeItem.objects.get(changeset__version=3)
    assert (item.key, item.definition) == ("pricing.allow_backorders", definition)
    assert (item.old_value, item.new_value, item.reset) == (True, None, True)
    document = Snapshot.objects.get(version=3).document
    assert "allow_backorders" not in document["groups"]["pricing"]
    assert document["groups"]["pricing"]["vat_rate"] == 0.2
    assert document["overridden"] == ["pricing.vat_rate"]


def test_environment_from_settings() -> None:
    sync()
    assert Snapshot.objects.get().document["environment"] == ""
    with override_settings(TUNABLES={"CATALOGUE": f"{__name__}.extended", "ENVIRONMENT": "staging"}):
        sync()
    assert Snapshot.objects.get(version=1).document["environment"] == "staging"


def test_is_synced() -> None:
    assert is_synced() is False
    sync()
    assert is_synced() is True
    with use("extended"):
        assert is_synced() is False
        sync()
        assert is_synced() is True
    assert is_synced() is False
