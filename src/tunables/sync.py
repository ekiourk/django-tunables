from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from tunables.catalogue import Catalogue, Group, Tunable
from tunables.errors import ConstraintError
from tunables.models import (
    ActorSource,
    ChangeItem,
    ChangeSet,
    ChangeSource,
    State,
    TunableDefinition,
    TunableValue,
)
from tunables.registry import get_catalogue
from tunables.services import write_snapshot


@dataclass(frozen=True)
class SyncResult:
    created: bool
    rebuilt: bool
    version: int


def is_synced() -> bool:
    """True when State exists and its catalogue_version matches the code."""
    state = State.objects.filter(pk=1).first()
    return state is not None and state.catalogue_version == get_catalogue().version


@transaction.atomic
def sync() -> SyncResult:
    """Mirror the catalogue, bootstrap State and snapshot 0, rebuild on catalogue change. Idempotent."""
    catalogue = get_catalogue()
    now = timezone.now()
    state, created = State.objects.select_for_update().get_or_create(
        pk=1, defaults={"current_version": 0, "catalogue_version": catalogue.version}
    )
    _mirror(catalogue, now)
    if created:
        write_snapshot(catalogue, version=0, changeset=None, created_at=now)
        return SyncResult(created=True, rebuilt=False, version=0)
    if state.catalogue_version == catalogue.version:
        return SyncResult(created=False, rebuilt=False, version=state.current_version)
    version = state.current_version + 1
    changeset = ChangeSet.objects.create(
        version=version,
        created_at=now,
        actor="system",
        actor_source=ActorSource.SYSTEM,
        source=ChangeSource.SYSTEM,
        reason="catalogue changed",
        catalogue_version=catalogue.version,
    )
    _drop_stale_overrides(catalogue, changeset)
    _drop_invalid_overrides(catalogue, changeset)
    write_snapshot(catalogue, version=version, changeset=changeset, created_at=now)
    state.current_version = version
    state.catalogue_version = catalogue.version
    state.save()
    return SyncResult(created=False, rebuilt=True, version=version)


def _mirror(catalogue: Catalogue, now: datetime) -> None:
    for group in catalogue.groups.values():
        for order, tunable in enumerate(group.tunables):
            key = f"{group.name}.{tunable.name}"
            TunableDefinition.objects.update_or_create(key=key, defaults=_definition_fields(group, order, tunable, now))
    TunableDefinition.objects.filter(is_active=True).exclude(key__in=catalogue.keys()).update(
        is_active=False, synced_at=now
    )


def _definition_fields(group: Group, order: int, tunable: Tunable, now: datetime) -> dict[str, Any]:
    described = tunable.type.describe()
    return {
        "group_name": group.name,
        "name": tunable.name,
        "order": order,
        "type_name": described["name"],
        "type_params": described["params"],
        "default": tunable.type.to_json(tunable.default),
        "title": str(tunable.title),
        "description": str(tunable.description),
        "unit": tunable.unit,
        "ui": dict(tunable.ui),
        "metadata": dict(tunable.metadata),
        "deprecated": tunable.deprecated,
        "is_active": True,
        "synced_at": now,
    }


def _drop_stale_overrides(catalogue: Catalogue, changeset: ChangeSet) -> None:
    for row in TunableValue.objects.exclude(key__in=catalogue.keys()):
        _reset_override(row, changeset)


def _drop_invalid_overrides(catalogue: Catalogue, changeset: ChangeSet) -> None:
    """Reset overrides whose stored value no longer coerces and validates under the current catalogue."""
    for row in TunableValue.objects.filter(key__in=catalogue.keys()):
        tunable = catalogue.get(row.key)
        try:
            tunable.type.validate(tunable.type.coerce(row.value))
        except ConstraintError:
            _reset_override(row, changeset)


def _reset_override(row: TunableValue, changeset: ChangeSet) -> None:
    ChangeItem.objects.create(
        changeset=changeset, key=row.key, definition=row.definition, old_value=row.value, new_value=None, reset=True
    )
    row.delete()
