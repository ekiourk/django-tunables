from collections.abc import Callable
from typing import Any

import pytest
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.db.models import Model

from tunables.errors import HistoryIsAppendOnly
from tunables.models import (
    ActorSource,
    ChangeItem,
    ChangeSet,
    ChangeSource,
    Snapshot,
    State,
    TunableDefinition,
    TunableValue,
)

pytestmark = pytest.mark.django_db


def test_migrations_are_up_to_date() -> None:
    call_command("makemigrations", "tunables", "--check", "--dry-run", verbosity=0)


def test_reader_contract_table_and_column_names() -> None:
    assert State._meta.db_table == "tunables_state"
    assert State._meta.get_field("current_version").column == "current_version"
    assert Snapshot._meta.db_table == "tunables_snapshot"
    for column in ("version", "document", "created_at"):
        assert Snapshot._meta.get_field(column).column == column


def test_state_is_a_singleton() -> None:
    State(current_version=1).save()
    State(current_version=2).save()
    assert State.objects.count() == 1
    state = State.objects.get()
    assert state.pk == 1
    assert state.current_version == 2


def test_state_check_constraint_rejects_other_pks() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        State.objects.bulk_create([State(id=2, current_version=0)])


def test_state_is_updatable() -> None:
    state = State.objects.create(current_version=0)
    state.current_version = 5
    state.save()
    assert State.objects.get().current_version == 5
    State.objects.update(current_version=6)
    assert State.objects.get().current_version == 6


def make_changeset(version: int = 1) -> ChangeSet:
    return ChangeSet.objects.create(
        version=version,
        actor="alice",
        actor_source=ActorSource.VERIFIED,
        source=ChangeSource.API,
        catalogue_version="sha256:test",
    )


def make_item(version: int = 1) -> ChangeItem:
    return ChangeItem.objects.create(changeset=make_changeset(version), key="pricing.vat_rate", new_value=0.2)


def make_snapshot(version: int = 1) -> Snapshot:
    return Snapshot.objects.create(version=version, format_version=1, catalogue_version="sha256:test", document={})


HISTORY: list[tuple[Callable[[int], Model], str, Any]] = [
    (make_changeset, "reason", "edited"),
    (make_item, "new_value", 0.5),
    (make_snapshot, "document", {"edited": True}),
]


@pytest.mark.parametrize(("make", "field", "new_value"), HISTORY)
def test_saving_a_loaded_history_row_is_refused(make: Callable[[int], Model], field: str, new_value: Any) -> None:
    row = make(1)
    loaded = type(row)._default_manager.get(pk=row.pk)
    setattr(loaded, field, new_value)
    with pytest.raises(HistoryIsAppendOnly):
        loaded.save()
    assert getattr(type(row)._default_manager.get(pk=row.pk), field) == getattr(row, field)


@pytest.mark.parametrize(("make", "field", "new_value"), HISTORY)
def test_saving_a_history_row_twice_is_refused(make: Callable[[int], Model], field: str, new_value: Any) -> None:
    row = make(1)
    setattr(row, field, new_value)
    with pytest.raises(HistoryIsAppendOnly):
        row.save()


@pytest.mark.parametrize(("make", "field", "new_value"), HISTORY)
def test_deleting_a_history_row_is_refused(make: Callable[[int], Model], field: str, new_value: Any) -> None:
    row = make(1)
    with pytest.raises(HistoryIsAppendOnly):
        row.delete()
    assert type(row)._default_manager.filter(pk=row.pk).exists()


@pytest.mark.parametrize(("make", "field", "new_value"), HISTORY)
def test_queryset_update_is_refused(make: Callable[[int], Model], field: str, new_value: Any) -> None:
    row = make(1)
    with pytest.raises(HistoryIsAppendOnly):
        type(row)._default_manager.filter(pk=row.pk).update(**{field: new_value})
    assert getattr(type(row)._default_manager.get(pk=row.pk), field) == getattr(row, field)


@pytest.mark.parametrize(("make", "field", "new_value"), HISTORY)
def test_queryset_delete_is_refused(make: Callable[[int], Model], field: str, new_value: Any) -> None:
    row = make(1)
    with pytest.raises(HistoryIsAppendOnly):
        type(row)._default_manager.all().delete()
    assert type(row)._default_manager.filter(pk=row.pk).exists()


def test_new_changeset_with_existing_version_is_an_integrity_error() -> None:
    make_changeset(1)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_changeset(1)


def test_new_snapshot_with_existing_pk_does_not_overwrite() -> None:
    first = make_snapshot(1)
    with pytest.raises(IntegrityError), transaction.atomic():
        Snapshot(pk=first.pk, version=2, format_version=1, catalogue_version="x", document={}).save()
    assert Snapshot.objects.get(pk=first.pk).version == 1


def test_definition_key_is_unique(definition: TunableDefinition) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        TunableDefinition.objects.create(
            key=definition.key,
            group_name="pricing",
            name="vat_rate",
            order=1,
            type_name="float",
            type_params={},
            default=0.0,
            synced_at=definition.synced_at,
        )


def test_value_key_is_unique(definition: TunableDefinition, changeset: ChangeSet) -> None:
    TunableValue.objects.create(key=definition.key, definition=definition, value=0.2, changeset=changeset)
    with pytest.raises(IntegrityError), transaction.atomic():
        TunableValue.objects.create(key=definition.key, definition=definition, value=0.3, changeset=changeset)


def test_value_upsert(definition: TunableDefinition, changeset: ChangeSet) -> None:
    TunableValue.objects.create(key=definition.key, definition=definition, value=0.2, changeset=changeset)
    later = make_changeset(2)
    TunableValue.objects.update_or_create(
        key=definition.key, defaults={"definition": definition, "value": 0.3, "changeset": later}
    )
    assert TunableValue.objects.count() == 1
    value = TunableValue.objects.get()
    assert value.value == 0.3
    assert value.changeset == later


def test_change_item_unique_per_changeset_and_key(changeset: ChangeSet) -> None:
    ChangeItem.objects.create(changeset=changeset, key="pricing.vat_rate", new_value=0.2)
    with pytest.raises(IntegrityError), transaction.atomic():
        ChangeItem.objects.create(changeset=changeset, key="pricing.vat_rate", new_value=0.3)


def test_snapshot_version_zero_has_no_changeset() -> None:
    snapshot = Snapshot.objects.create(version=0, format_version=1, catalogue_version="sha256:test", document={})
    assert snapshot.changeset is None


def test_tag_names_are_unique_and_validated() -> None:
    from django.core.exceptions import ValidationError

    from tunables.models import Tag

    Tag.objects.create(name="money")
    with pytest.raises(IntegrityError), transaction.atomic():
        Tag.objects.create(name="money")
    for bad in ("Money", "-x", "a b", ""):
        with pytest.raises(ValidationError):
            Tag(name=bad).full_clean()
    Tag(name="q3-2026_a").full_clean()
    assert Tag.objects.get(name="money").from_catalogue is False


def test_definition_defaults_to_the_general_category(definition: TunableDefinition) -> None:
    assert definition.category_name == "general"
    assert list(definition.tags.all()) == []


def test_tag_assignments_through_the_seeded_flag(definition: TunableDefinition) -> None:
    from tunables.models import Tag, TunableDefinitionTag

    money = Tag.objects.create(name="money", from_catalogue=True)
    manual = Tag.objects.create(name="review")
    TunableDefinitionTag.objects.create(definition=definition, tag=money, seeded=True)
    TunableDefinitionTag.objects.create(definition=definition, tag=manual)
    assert sorted(definition.tags.values_list("name", flat=True)) == ["money", "review"]
    assert list(money.definitions.all()) == [definition]
    assert TunableDefinitionTag.objects.get(tag=money).seeded is True
    assert TunableDefinitionTag.objects.get(tag=manual).seeded is False
    with pytest.raises(IntegrityError), transaction.atomic():
        TunableDefinitionTag.objects.create(definition=definition, tag=money)
    manual.delete()
    assert list(definition.tags.values_list("name", flat=True)) == ["money"]
    assert TunableDefinitionTag.objects.count() == 1


def test_migration_0005_marks_existing_seeded_rows(db: None) -> None:
    from importlib import import_module

    module = import_module("tunables.migrations.0005_tunabledefinitiontag_assigned_at_and_more")
    names = [type(operation).__name__ for operation in module.Migration.operations]
    assert "RunPython" in names
