from dataclasses import replace
from typing import Any

import pytest
from django.test import override_settings

from tests.catalogue import CATEGORIES, catalogue, currencies_within_limit, limits, pricing, thermostat, weights
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


def alt(groups: Any, **kwargs: Any) -> Catalogue:
    """An alternative catalogue built from the shared groups, with the shared categories."""
    return Catalogue(groups, categories=CATEGORIES, **kwargs)


reworded = alt([replace(pricing, title="Prices"), thermostat, weights, limits])
extended = alt(
    [
        replace(pricing, tunables=[*pricing.tunables, Tunable("discount", Float(min=0.0, max=1.0), 0.0)]),
        thermostat,
        weights,
        limits,
    ]
)
reduced = alt([replace(pricing, tunables=pricing.tunables[:-1]), thermostat, weights, limits])


def retype(group: Group, tunable: Tunable) -> Group:
    return replace(group, tunables=[tunable if t.name == tunable.name else t for t in group.tunables])


retyped = alt([retype(pricing, Tunable("vat_rate", Integer(min=0, max=10), 0)), thermostat, weights, limits])
narrowed = alt([pricing, retype(thermostat, Tunable("target_c", Float(min=5.0, max=25.0), 21.0)), weights, limits])


def alpha_below_point_four(values: Any) -> None:
    """alpha must stay below 0.4."""
    if values["alpha"] >= 0.4:
        raise ConstraintError("group", "alpha must be below 0.4")


def at_most_one_currency(values: Any) -> None:
    """At most one currency may be accepted."""
    if len(values["pricing"]["currencies"]) > 1:
        raise ConstraintError("catalogue", "only one currency may be accepted")


strict_weights = alt(
    [pricing, thermostat, replace(weights, validators=[*weights.validators, alpha_below_point_four]), limits],
    validators=[currencies_within_limit],
)
strict_limits = alt([pricing, thermostat, weights, limits], validators=[currencies_within_limit, at_most_one_currency])
strict_limits_extended = alt(
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


def _tunable_named(group: Group, name: str) -> Tunable:
    return next(t for t in group.tunables if t.name == name)


def retag(group: Group, name: str, tags: list[str]) -> Group:
    return retype(group, replace(_tunable_named(group, name), tags=tags))


no_comfort = alt(
    [pricing, retag(retag(thermostat, "target_c", []), "mode", []), weights, limits],
    validators=[currencies_within_limit],
)
alpha_money = alt(
    [pricing, thermostat, retag(weights, "alpha", ["money"]), limits], validators=[currencies_within_limit]
)
weights_in_shop = alt(
    [pricing, thermostat, replace(weights, category="shop"), limits], validators=[currencies_within_limit]
)


def seeded_pairs() -> set[tuple[str, str, bool]]:
    from tunables.models import TunableDefinitionTag

    return {
        (r.definition.key, r.tag.name, r.seeded)
        for r in TunableDefinitionTag.objects.select_related("definition", "tag")
    }


def tag_flags() -> dict[str, bool]:
    from tunables.models import Tag

    return {t.name: t.from_catalogue for t in Tag.objects.all()}


SEEDED = {
    ("pricing.vat_rate", "money", True),
    ("pricing.shipping_rates", "money", True),
    ("limits.max_currencies", "money", True),
    ("thermostat.target_c", "comfort", True),
    ("thermostat.mode", "comfort", True),
}


def test_sync_mirrors_categories_and_seeds_tags() -> None:
    sync()
    categories = dict(TunableDefinition.objects.values_list("key", "category_name"))
    assert categories["pricing.vat_rate"] == "shop"
    assert categories["thermostat.mode"] == "building"
    assert categories["weights.alpha"] == "general"
    assert categories["limits.max_currencies"] == "general"
    assert tag_flags() == {"money": True, "comfort": True}
    assert seeded_pairs() == SEEDED
    assert sync() == SyncResult(created=False, rebuilt=False, version=0)
    assert seeded_pairs() == SEEDED


def test_manual_tags_survive_sync() -> None:
    from tunables.models import Tag, TunableDefinitionTag

    sync()
    review = Tag.objects.create(name="review")
    TunableDefinitionTag.objects.create(definition=TunableDefinition.objects.get(key="weights.beta"), tag=review)
    sync()
    assert tag_flags()["review"] is False
    assert ("weights.beta", "review", False) in seeded_pairs()


def test_dropped_seed_removes_assignment_and_keeps_the_tag() -> None:
    sync()
    with use("no_comfort"):
        sync()
    assert tag_flags() == {"money": True, "comfort": False}
    assert seeded_pairs() == {pair for pair in SEEDED if pair[1] == "money"}


def test_added_seed_creates_or_flips_the_assignment() -> None:
    from tunables.models import Tag, TunableDefinitionTag

    sync()
    TunableDefinitionTag.objects.create(
        definition=TunableDefinition.objects.get(key="weights.alpha"), tag=Tag.objects.get(name="money")
    )
    assert ("weights.alpha", "money", False) in seeded_pairs()
    with use("alpha_money"):
        sync()
    assert ("weights.alpha", "money", True) in seeded_pairs()
    assert TunableDefinitionTag.objects.filter(definition__key="weights.alpha").count() == 1


def test_moving_a_group_updates_the_category() -> None:
    sync()
    with use("weights_in_shop"):
        assert sync().rebuilt is False
    assert TunableDefinition.objects.get(key="weights.alpha").category_name == "shop"


def test_mirror_drift_and_check() -> None:
    from io import StringIO

    from django.core.management import CommandError, call_command

    from tunables.sync import mirror_drift

    sync()
    assert mirror_drift() == []
    with use("weights_in_shop"):
        drift = mirror_drift()
        assert drift == [
            f"weights.{name}: category 'general' in the mirror, 'shop' in code" for name in ("alpha", "beta", "gamma")
        ]
        assert is_synced() is True
        err = StringIO()
        with pytest.raises(CommandError) as info:
            call_command("tunables_sync", "--check", stdout=StringIO(), stderr=err)
        assert info.value.returncode == 1
        assert "weights.alpha: category" in str(info.value)
    with use("alpha_money"):
        assert mirror_drift() == ["weights.alpha: seed tag 'money' is not assigned"]
    with use("no_comfort"):
        assert sorted(mirror_drift()) == [
            "comfort: tag is marked as coming from the catalogue but no tunable seeds it",
            "thermostat.mode: assignment of 'comfort' is seeded but not in code",
            "thermostat.target_c: assignment of 'comfort' is seeded but not in code",
        ]
