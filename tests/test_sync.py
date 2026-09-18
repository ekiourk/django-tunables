from dataclasses import replace
from typing import Any

import pytest
from django.test import override_settings

from tests.catalogue import catalogue, currencies_within_limit, limits, pricing, thermostat, weights
from tunables import Actor, Catalogue, Change, Float, Group, Integer, Tunable, services
from tunables.document import FORMAT_VERSION, build_document
from tunables.errors import CatalogueValidationError, ConstraintError, GroupError
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

reworded = Catalogue([replace(pricing, title="Prices"), thermostat, weights, limits])
extended = Catalogue(
    [
        replace(pricing, tunables=[*pricing.tunables, Tunable("discount", Float(min=0.0, max=1.0), 0.0)]),
        thermostat,
        weights,
        limits,
    ]
)
reduced = Catalogue([replace(pricing, tunables=pricing.tunables[:-1]), thermostat, weights, limits])


def retype(group: Group, tunable: Tunable) -> Group:
    return replace(group, tunables=[tunable if t.name == tunable.name else t for t in group.tunables])


retyped = Catalogue([retype(pricing, Tunable("vat_rate", Integer(min=0, max=10), 0)), thermostat, weights, limits])
narrowed = Catalogue(
    [pricing, retype(thermostat, Tunable("target_c", Float(min=5.0, max=25.0), 21.0)), weights, limits]
)


def alpha_below_point_four(values: Any) -> None:
    """alpha must stay below 0.4."""
    if values["alpha"] >= 0.4:
        raise ConstraintError("group", "alpha must be below 0.4")


def at_most_one_currency(values: Any) -> None:
    """At most one currency may be accepted."""
    if len(values["pricing"]["currencies"]) > 1:
        raise ConstraintError("catalogue", "only one currency may be accepted")


strict_weights = Catalogue(
    [pricing, thermostat, replace(weights, validators=[*weights.validators, alpha_below_point_four]), limits],
    validators=[currencies_within_limit],
)
strict_limits = Catalogue(
    [pricing, thermostat, weights, limits], validators=[currencies_within_limit, at_most_one_currency]
)
strict_limits_extended = Catalogue(
    [
        replace(pricing, tunables=[*pricing.tunables, Tunable("discount", Float(min=0.0, max=1.0), 0.0)]),
        thermostat,
        weights,
        limits,
    ],
    validators=[currencies_within_limit, at_most_one_currency],
)


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
    assert TunableDefinition.objects.count() == 14
    assert TunableDefinition.objects.filter(is_active=True).count() == 14
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
    assert TunableDefinition.objects.count() == 14


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
    assert TunableDefinition.objects.count() == 15


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


def test_retyped_tunable_resets_its_invalid_override_and_keeps_valid_ones() -> None:
    sync()
    override("pricing.vat_rate", 0.2)
    override("thermostat.mode", "heat")
    with use("retyped"):
        assert sync() == SyncResult(created=False, rebuilt=True, version=3)
        item = ChangeItem.objects.get(changeset__version=3)
        assert (item.key, item.reset, item.old_value, item.new_value) == ("pricing.vat_rate", True, 0.2, None)
        assert item.definition == TunableDefinition.objects.get(key="pricing.vat_rate")
        assert not TunableValue.objects.filter(key="pricing.vat_rate").exists()
        assert TunableValue.objects.get(key="thermostat.mode").value == "heat"
        document = Snapshot.objects.get(version=3).document
        assert document["groups"]["pricing"]["vat_rate"] == 0
        assert document["overridden"] == ["thermostat.mode"]
        values = services.current_values()
        assert (values["pricing"]["vat_rate"], values["thermostat"]["mode"]) == (0, "heat")
        sibling = services.apply_changeset(
            [Change("pricing.allow_backorders", True)], actor=Actor("alice", "verified"), source="api"
        )
        assert sibling.version == 4


def test_narrowed_bound_resets_out_of_range_override() -> None:
    sync()
    override("thermostat.target_c", 28.0)
    with use("narrowed"):
        assert sync() == SyncResult(created=False, rebuilt=True, version=2)
        item = ChangeItem.objects.get(changeset__version=2)
        assert (item.key, item.reset, item.old_value) == ("thermostat.target_c", True, 28.0)
        document = Snapshot.objects.get(version=2).document
        assert document["groups"]["thermostat"]["target_c"] == 21.0
        assert document["overridden"] == []
        assert services.current_values()["thermostat"]["target_c"] == 21.0


def test_state_is_locked_before_the_mirror_runs() -> None:
    from unittest import mock

    from tunables import sync as sync_module

    seen: list[bool] = []
    original = sync_module._mirror

    def observing_mirror(*args: Any) -> None:
        seen.append(State.objects.filter(pk=1).exists())
        original(*args)

    with mock.patch.object(sync_module, "_mirror", observing_mirror):
        sync()
    assert seen == [True], "the State row must exist and be locked before the catalogue is mirrored"


def test_snapshot_zero_equals_the_defaults_document() -> None:
    from tunables.document import defaults_document

    sync()
    snapshot = Snapshot.objects.get(version=0)
    assert snapshot.document == defaults_document(catalogue, created_at=snapshot.created_at)


def test_written_snapshots_validate_against_the_document_schema() -> None:
    from jsonschema import Draft202012Validator

    from tunables.schema import document_schema

    sync()
    override("pricing.vat_rate", 0.2)
    validator = Draft202012Validator(document_schema(catalogue), format_checker=Draft202012Validator.FORMAT_CHECKER)
    for snapshot in Snapshot.objects.all():
        validator.validate(snapshot.document)


def test_sync_result_reports_rule_violations() -> None:
    assert sync().violations == ()
    assert sync().violations == ()
    with use("strict_weights"):
        result = sync()
    assert (result.rebuilt, result.violations) == (False, (GroupError("weights", "group", "alpha must be below 0.4"),))
    override("pricing.currencies", ["EUR", "USD"])
    with use("strict_limits_extended"):
        result = sync()
    assert result.rebuilt is True
    assert result.violations == (CatalogueValidationError("catalogue", "only one currency may be accepted"),)
    assert Snapshot.objects.get(version=result.version).document["groups"]["pricing"]["currencies"] == ["EUR", "USD"]
