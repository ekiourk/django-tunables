from typing import Any

import pytest

from tests.catalogue import catalogue
from tests.test_sync import override
from tunables import Actor, Change, services
from tunables.errors import (
    CatalogueOutOfSync,
    CatalogueValidationError,
    FieldError,
    FieldWarning,
    GroupError,
    NothingToChange,
    UnknownVersion,
    ValidationFailed,
    VersionConflict,
)
from tunables.models import ChangeItem, ChangeSet, Snapshot, State, TunableDefinition, TunableValue
from tunables.services import ChangeResult
from tunables.sync import SyncResult

pytestmark = pytest.mark.django_db


def test_current_version_requires_sync() -> None:
    with pytest.raises(CatalogueOutOfSync):
        services.current_version()


def test_current_version_after_sync(synced: SyncResult) -> None:
    assert services.current_version() == 0
    override("pricing.vat_rate", 0.2)
    assert services.current_version() == 1


def test_current_values_are_defaults_without_overrides(synced: SyncResult) -> None:
    assert services.current_values() == catalogue.defaults()


def test_current_values_overlay_coerced_overrides(synced: SyncResult) -> None:
    override("pricing.vat_rate", 0.2)
    override("thermostat.display_colour", "#ABCDEF")
    values = services.current_values()
    assert values["pricing"]["vat_rate"] == 0.2
    assert values["thermostat"]["display_colour"] == "#abcdef"
    assert values["pricing"]["free_shipping_over"] == 50.0


ALICE = Actor("alice", "verified", client="cli")


def apply(*changes: Change, **kwargs: Any) -> ChangeResult:
    kwargs.setdefault("actor", ALICE)
    kwargs.setdefault("source", "api")
    return services.apply_changeset(list(changes), **kwargs)


def counts() -> tuple[int, int, int, int]:
    return (
        ChangeSet.objects.count(),
        ChangeItem.objects.count(),
        TunableValue.objects.count(),
        Snapshot.objects.count(),
    )


def test_apply_happy_path(synced: SyncResult) -> None:
    result = apply(
        Change("pricing.vat_rate", 0.2),
        Change("thermostat.mode", "heat"),
        reason="winter",
        request_id="req-1",
        metadata={"ticket": 42},
    )
    assert result.version == 1
    assert result.warnings == ()
    changeset = result.changeset
    assert changeset == ChangeSet.objects.get(version=1)
    assert (changeset.actor, changeset.actor_source, changeset.client) == ("alice", "verified", "cli")
    assert (changeset.source, changeset.reason, changeset.request_id) == ("api", "winter", "req-1")
    assert (changeset.metadata, changeset.catalogue_version) == ({"ticket": 42}, catalogue.version)
    assert changeset.restores_version is None
    items = {item.key: item for item in changeset.items.all()}
    assert set(items) == {"pricing.vat_rate", "thermostat.mode"}
    assert (items["pricing.vat_rate"].old_value, items["pricing.vat_rate"].new_value) == (None, 0.2)
    assert items["pricing.vat_rate"].reset is False
    assert items["pricing.vat_rate"].definition == TunableDefinition.objects.get(key="pricing.vat_rate")
    values = {row.key: row for row in TunableValue.objects.all()}
    assert values["thermostat.mode"].value == "heat"
    assert values["thermostat.mode"].changeset == changeset
    snapshot = result.snapshot
    assert snapshot == Snapshot.objects.get(version=1)
    assert snapshot.changeset == changeset
    assert snapshot.document["groups"]["pricing"]["vat_rate"] == 0.2
    assert snapshot.document["groups"]["thermostat"]["mode"] == "heat"
    assert snapshot.document["overridden"] == ["pricing.vat_rate", "thermostat.mode"]
    assert snapshot.document["created_at"] == snapshot.created_at.isoformat() == changeset.created_at.isoformat()
    assert State.objects.get().current_version == 1
    assert services.current_version() == 1


def test_changing_an_overridden_key_records_previous_override(synced: SyncResult) -> None:
    apply(Change("pricing.vat_rate", 0.2))
    result = apply(Change("pricing.vat_rate", 0.1))
    item = result.changeset.items.get()
    assert (item.old_value, item.new_value) == (0.2, 0.1)
    assert TunableValue.objects.count() == 1
    row = TunableValue.objects.get(key="pricing.vat_rate")
    assert (row.value, row.changeset) == (0.1, result.changeset)


def test_reset_removes_override(synced: SyncResult) -> None:
    apply(Change("pricing.vat_rate", 0.2))
    result = apply(Change("pricing.vat_rate", reset=True))
    item = result.changeset.items.get()
    assert (item.old_value, item.new_value, item.reset) == (0.2, None, True)
    assert not TunableValue.objects.filter(key="pricing.vat_rate").exists()
    assert result.snapshot.document["groups"]["pricing"]["vat_rate"] == 0.24
    assert result.snapshot.document["overridden"] == []


def test_expected_version_mismatch(synced: SyncResult) -> None:
    apply(Change("pricing.vat_rate", 0.2))
    before = counts()
    with pytest.raises(VersionConflict) as info:
        apply(Change("pricing.vat_rate", 0.1), expected_version=0)
    assert (info.value.expected, info.value.actual) == (0, 1)
    assert counts() == before
    apply(Change("pricing.vat_rate", 0.1), expected_version=1)


def test_all_field_errors_are_reported_and_nothing_written(synced: SyncResult) -> None:
    before = counts()
    with pytest.raises(ValidationFailed) as info:
        apply(
            Change("pricing.discount", 1),
            Change("pricing.vat_rate", "abc"),
            Change("thermostat.target_c", 100.0),
            Change("thermostat.mode", "eco"),
        )
    assert info.value.errors == [
        FieldError("pricing.discount", "unknown_key", "unknown tunable 'pricing.discount'"),
        FieldError("pricing.vat_rate", "type", "expected a number"),
        FieldError("thermostat.target_c", "max", "must be <= 30.0"),
        FieldError("thermostat.mode", "enum", "must be one of: auto, heat, cool, off"),
    ]
    assert str(info.value) == "4 errors"
    assert counts() == before


def test_group_validator_failure_writes_nothing(synced: SyncResult) -> None:
    before = counts()
    with pytest.raises(ValidationFailed) as info:
        apply(Change("weights.alpha", 0.9))
    assert info.value.errors == [GroupError("weights", "group", "weights must sum to 1")]
    assert counts() == before
    with pytest.raises(ValidationFailed) as info:
        apply(Change("weights.alpha", 0.9), Change("pricing.vat_rate", 2.0))
    assert info.value.errors == [
        FieldError("pricing.vat_rate", "max", "must be <= 1.0"),
        GroupError("weights", "group", "weights must sum to 1"),
    ]


def test_group_validator_sees_proposed_and_unchanged_values(synced: SyncResult) -> None:
    result = apply(Change("weights.alpha", 0.6), Change("weights.beta", 0.2))
    assert result.snapshot.document["groups"]["weights"] == {"alpha": 0.6, "beta": 0.2, "gamma": 0.2}
    apply(Change("weights.gamma", 0.1), Change("weights.alpha", 0.7))


def test_nothing_to_change(synced: SyncResult) -> None:
    with pytest.raises(NothingToChange):
        apply()
    with pytest.raises(NothingToChange):
        apply(Change("pricing.vat_rate", 0.24))
    with pytest.raises(NothingToChange):
        apply(Change("pricing.vat_rate", reset=True))
    apply(Change("thermostat.mode", "heat"))
    with pytest.raises(NothingToChange):
        apply(Change("thermostat.mode", "heat"))
    assert counts() == (1, 1, 1, 2)


def test_no_op_changes_are_dropped_from_a_mixed_list(synced: SyncResult) -> None:
    result = apply(Change("pricing.vat_rate", 0.24), Change("thermostat.mode", "heat"))
    assert [item.key for item in result.changeset.items.all()] == ["thermostat.mode"]
    assert result.snapshot.document["overridden"] == ["thermostat.mode"]


def test_duplicate_key_and_null_value(synced: SyncResult) -> None:
    with pytest.raises(ValidationFailed) as info:
        apply(Change("pricing.vat_rate", 0.2), Change("pricing.vat_rate", 0.3), Change("thermostat.mode", None))
    assert info.value.errors == [
        FieldError("pricing.vat_rate", "duplicate", "key appears more than once"),
        FieldError("thermostat.mode", "type", "expected a string"),
    ]


def test_deprecated_no_op_does_not_warn(synced: SyncResult) -> None:
    assert services.validate([Change("thermostat.legacy_offset", 0.0), Change("pricing.vat_rate", 0.2)]) == []


def test_deprecated_key_is_applied_with_a_warning(synced: SyncResult) -> None:
    result = apply(Change("thermostat.legacy_offset", 1.5))
    assert result.snapshot.document["groups"]["thermostat"]["legacy_offset"] == 1.5
    assert result.warnings == (
        FieldWarning("thermostat.legacy_offset", "deprecated", "deprecated: Use target_c instead."),
    )


def test_out_of_sync_state(synced: SyncResult) -> None:
    State.objects.update(catalogue_version="sha256:stale")
    with pytest.raises(CatalogueOutOfSync):
        apply(Change("pricing.vat_rate", 0.2))
    assert counts() == (0, 0, 0, 1)


def test_missing_state(db: None) -> None:
    with pytest.raises(CatalogueOutOfSync):
        apply(Change("pricing.vat_rate", 0.2))


def test_setting_an_overridden_key_to_its_default_resets_it(synced: SyncResult) -> None:
    apply(Change("pricing.vat_rate", 0.2))
    result = apply(Change("pricing.vat_rate", 0.24))
    item = result.changeset.items.get()
    assert (item.key, item.reset, item.old_value, item.new_value) == ("pricing.vat_rate", True, 0.2, None)
    assert not TunableValue.objects.filter(key="pricing.vat_rate").exists()
    assert result.snapshot.document["overridden"] == []
    assert result.snapshot.document["groups"]["pricing"]["vat_rate"] == 0.24


def test_setting_a_default_key_to_its_default_is_a_no_op(synced: SyncResult) -> None:
    with pytest.raises(NothingToChange):
        apply(Change("pricing.vat_rate", 0.24))
    result = apply(Change("pricing.vat_rate", 0.24), Change("thermostat.mode", "heat"))
    assert [item.key for item in result.changeset.items.all()] == ["thermostat.mode"]


def test_validate(synced: SyncResult) -> None:
    before = counts()
    assert services.validate([Change("pricing.vat_rate", 0.2)]) == []
    assert services.validate([Change("thermostat.legacy_offset", 1.5)]) == [
        FieldWarning("thermostat.legacy_offset", "deprecated", "deprecated: Use target_c instead.")
    ]
    with pytest.raises(ValidationFailed) as info:
        services.validate([Change("pricing.vat_rate", 2.0), Change("weights.alpha", 0.9)])
    assert [type(e) for e in info.value.errors] == [FieldError, GroupError]
    with pytest.raises(NothingToChange):
        services.validate([Change("pricing.vat_rate", 0.24)])
    assert counts() == before
    assert State.objects.get().current_version == 0


def test_rollback_restores_overrides_of_target_version(synced: SyncResult) -> None:
    apply(Change("pricing.vat_rate", 0.2), Change("thermostat.mode", "heat"))
    apply(Change("pricing.vat_rate", 0.1), Change("pricing.currencies", ["USD"]))
    result = services.rollback(1, actor=ALICE, reason="undo")
    assert result.version == 3
    changeset = result.changeset
    assert (changeset.source, changeset.restores_version, changeset.reason) == ("rollback", 1, "undo")
    assert (changeset.actor, changeset.actor_source, changeset.client) == ("alice", "verified", "cli")
    items = {item.key: item for item in changeset.items.all()}
    assert set(items) == {"pricing.vat_rate", "pricing.currencies"}
    assert (items["pricing.vat_rate"].old_value, items["pricing.vat_rate"].new_value) == (0.1, 0.2)
    assert (items["pricing.currencies"].old_value, items["pricing.currencies"].reset) == (["USD"], True)
    target = Snapshot.objects.get(version=1).document
    assert result.snapshot.document["groups"] == target["groups"]
    assert result.snapshot.document["overridden"] == target["overridden"]
    assert {row.key: row.value for row in TunableValue.objects.all()} == {
        "pricing.vat_rate": 0.2,
        "thermostat.mode": "heat",
    }


def test_rollback_to_zero_resets_everything(synced: SyncResult) -> None:
    apply(Change("pricing.vat_rate", 0.2), Change("thermostat.mode", "heat"))
    result = services.rollback(0, actor=ALICE)
    assert all(item.reset for item in result.changeset.items.all())
    assert result.changeset.items.count() == 2
    assert TunableValue.objects.count() == 0
    assert result.snapshot.document["overridden"] == []
    assert result.snapshot.document["groups"] == catalogue.defaults()


def test_rollback_to_current_or_unknown_version(synced: SyncResult) -> None:
    apply(Change("pricing.vat_rate", 0.2))
    with pytest.raises(NothingToChange):
        services.rollback(1, actor=ALICE)
    with pytest.raises(UnknownVersion) as info:
        services.rollback(7, actor=ALICE)
    assert info.value.version == 7
    with pytest.raises(VersionConflict):
        services.rollback(0, actor=ALICE, expected_version=0)
    assert ChangeSet.objects.count() == 1


def test_rollback_to_a_version_without_the_override_resets_it(synced: SyncResult) -> None:
    apply(Change("pricing.vat_rate", 0.2))
    apply(Change("pricing.vat_rate", 0.24))
    apply(Change("pricing.vat_rate", 0.3))
    result = services.rollback(2, actor=ALICE)
    item = result.changeset.items.get()
    assert (item.key, item.reset, item.old_value) == ("pricing.vat_rate", True, 0.3)
    assert TunableValue.objects.count() == 0
    assert result.snapshot.document["overridden"] == []


def test_rollback_ignores_keys_no_longer_in_catalogue(synced: SyncResult) -> None:
    apply(Change("pricing.vat_rate", 0.2))
    snapshot = Snapshot.objects.get(version=1)
    document = dict(snapshot.document)
    document["groups"] = {**document["groups"], "pricing": {**document["groups"]["pricing"], "gone": 1}}
    document["overridden"] = [*document["overridden"], "pricing.gone"]
    Snapshot._base_manager.filter(pk=snapshot.pk).update(document=document)
    apply(Change("pricing.vat_rate", 0.1))
    result = services.rollback(1, actor=ALICE)
    assert [item.key for item in result.changeset.items.all()] == ["pricing.vat_rate"]


def test_document_changes_lenient_and_strict(synced: SyncResult) -> None:
    document = {"format_version": 1, "groups": {"pricing": {"vat_rate": 0.2, "discount": 5}, "shop": {"open": True}}}
    changes, warnings = services.document_changes(document)
    assert changes == [Change("pricing.vat_rate", 0.2)]
    assert [(w.key, w.code) for w in warnings] == [("pricing.discount", "unknown_key"), ("shop.open", "unknown_key")]
    with pytest.raises(ValidationFailed) as info:
        services.document_changes(document, strict=True)
    assert [(e.key, e.code) for e in info.value.errors if isinstance(e, FieldError)] == [
        ("pricing.discount", "unknown_key"),
        ("shop.open", "unknown_key"),
    ]


def test_document_changes_rejects_other_formats(synced: SyncResult) -> None:
    with pytest.raises(ValidationFailed) as info:
        services.document_changes({"format_version": 2, "groups": {}})
    assert info.value.errors == [FieldError("format_version", "unsupported", "expected format_version 1")]
    with pytest.raises(ValidationFailed) as info:
        services.document_changes({"format_version": 1, "groups": {"pricing": [1, 2]}})
    assert info.value.errors == [FieldError("groups.pricing", "type", "expected an object")]
    with pytest.raises(ValidationFailed) as info:
        services.document_changes({"format_version": 1, "groups": [1, 2]})
    assert info.value.errors == [FieldError("groups", "type", "expected an object")]


def test_rollback_changes_matches_what_rollback_applies(synced: SyncResult) -> None:
    apply(Change("pricing.vat_rate", 0.2), Change("thermostat.mode", "heat"))
    apply(Change("pricing.vat_rate", 0.1), Change("pricing.currencies", ["USD"]))
    changes = services.rollback_changes(1)
    assert sorted(changes, key=lambda c: c.key) == [
        Change("pricing.currencies", reset=True),
        Change("pricing.vat_rate", 0.2),
    ]
    result = services.rollback(1, actor=ALICE)
    assert {item.key for item in result.changeset.items.all()} == {c.key for c in changes}
    with pytest.raises(UnknownVersion):
        services.rollback_changes(9)


def test_group_errors_report_an_uncoercible_stored_override(synced: SyncResult) -> None:
    changeset = apply(Change("pricing.vat_rate", 0.2)).changeset
    TunableValue.objects.create(
        key="weights.alpha",
        definition=TunableDefinition.objects.get(key="weights.alpha"),
        value="oops",
        changeset=changeset,
    )
    with pytest.raises(ValidationFailed) as info:
        services.validate([Change("weights.beta", 0.2)])
    assert info.value.errors == [GroupError("weights", "type", "expected a number")]
    with pytest.raises(ValidationFailed):
        apply(Change("weights.beta", 0.2))


def test_catalogue_validator_sees_effective_values_across_groups(synced: SyncResult) -> None:
    apply(Change("pricing.currencies", ["EUR", "USD"]))
    before = counts()
    with pytest.raises(ValidationFailed) as info:
        apply(Change("limits.max_currencies", 1))
    assert info.value.errors == [
        CatalogueValidationError("catalogue", "accepted currencies exceed limits.max_currencies")
    ]
    assert counts() == before
    with pytest.raises(ValidationFailed) as info:
        services.validate([Change("limits.max_currencies", 1)])
    assert isinstance(info.value.errors[0], CatalogueValidationError)
    apply(Change("limits.max_currencies", 2))


def test_catalogue_validator_runs_on_a_multi_group_write(synced: SyncResult) -> None:
    with pytest.raises(ValidationFailed) as info:
        apply(Change("pricing.currencies", ["EUR", "USD", "GBP"]), Change("limits.max_currencies", 2))
    assert [type(e) for e in info.value.errors] == [CatalogueValidationError]
    apply(Change("pricing.currencies", ["EUR", "USD", "GBP"]), Change("limits.max_currencies", 3))


def test_group_and_catalogue_errors_report_together(synced: SyncResult) -> None:
    apply(Change("pricing.currencies", ["EUR", "USD"]))
    with pytest.raises(ValidationFailed) as info:
        apply(Change("weights.alpha", 0.9), Change("limits.max_currencies", 1))
    assert [type(e) for e in info.value.errors] == [GroupError, CatalogueValidationError]


def test_document_changes_replace_resets_omitted_overrides(synced: SyncResult) -> None:
    apply(Change("pricing.vat_rate", 0.2), Change("thermostat.mode", "heat"))
    document = {"format_version": 1, "groups": {"pricing": {"vat_rate": 0.1}}}
    changes, warnings = services.document_changes(document, replace=True)
    assert sorted(changes, key=lambda c: c.key) == [
        Change("pricing.vat_rate", 0.1),
        Change("thermostat.mode", reset=True),
    ]
    assert warnings == []
    partial, _ = services.document_changes(document)
    assert partial == [Change("pricing.vat_rate", 0.1)]
    complete = {"format_version": 1, "groups": {"pricing": {"vat_rate": 0.2}, "thermostat": {"mode": "heat"}}}
    changes, _ = services.document_changes(complete, replace=True)
    with pytest.raises(NothingToChange):
        apply(*changes)


def test_diff_versions(synced: SyncResult) -> None:
    from tunables.services import DiffEntry, diff_versions

    apply(Change("pricing.vat_rate", 0.2), Change("thermostat.mode", "heat"))
    apply(
        Change("pricing.vat_rate", 0.1),
        Change("thermostat.mode", reset=True),
        Change("weights.alpha", 0.6),
        Change("weights.beta", 0.2),
    )
    assert diff_versions(1, 2) == [
        DiffEntry("pricing.vat_rate", 0.2, 0.1),
        DiffEntry("thermostat.mode", "heat", "auto"),
        DiffEntry("weights.alpha", 0.5, 0.6),
        DiffEntry("weights.beta", 0.3, 0.2),
    ]
    assert diff_versions(2, 1) == [
        DiffEntry("pricing.vat_rate", 0.1, 0.2),
        DiffEntry("thermostat.mode", "auto", "heat"),
        DiffEntry("weights.alpha", 0.6, 0.5),
        DiffEntry("weights.beta", 0.2, 0.3),
    ]
    assert diff_versions(0, 1) == [
        DiffEntry("pricing.vat_rate", 0.24, 0.2),
        DiffEntry("thermostat.mode", "auto", "heat"),
    ]
    assert diff_versions(2, 2) == []
    with pytest.raises(UnknownVersion) as info:
        diff_versions(1, 9)
    assert info.value.version == 9


def test_diff_versions_across_a_catalogue_change(synced: SyncResult) -> None:
    from django.test import override_settings

    from tests import test_sync
    from tunables.services import DiffEntry, diff_versions
    from tunables.sync import sync

    with override_settings(TUNABLES={"CATALOGUE": f"{test_sync.__name__}.extended"}):
        sync()
        assert diff_versions(0, 1) == [DiffEntry("pricing.discount", None, 0.0)]
        assert diff_versions(1, 0) == [DiffEntry("pricing.discount", 0.0, None)]


def test_rule_violations(synced: SyncResult) -> None:
    from tests.test_sync import use

    assert services.rule_violations() == []
    apply(Change("pricing.currencies", ["EUR", "USD"]))
    with use("strict_weights"):
        assert services.rule_violations() == [GroupError("weights", "group", "alpha must be below 0.4")]
    with use("strict_limits"):
        assert services.rule_violations() == [
            CatalogueValidationError("catalogue", "only one currency may be accepted")
        ]
    changeset = ChangeSet.objects.get(version=1)
    TunableValue.objects.create(
        key="weights.alpha",
        definition=TunableDefinition.objects.get(key="weights.alpha"),
        value="oops",
        changeset=changeset,
    )
    with use("strict_limits"):
        assert services.rule_violations() == [GroupError("weights", "type", "expected a number")]
