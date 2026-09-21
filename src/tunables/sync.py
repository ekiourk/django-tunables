from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from tunables.catalogue import Catalogue, Group, Tunable
from tunables.errors import CatalogueValidationError, ConstraintError, GroupError
from tunables.models import (
    ActorSource,
    ChangeItem,
    ChangeSet,
    ChangeSource,
    State,
    Tag,
    TunableDefinition,
    TunableDefinitionTag,
    TunableValue,
)
from tunables.registry import get_catalogue
from tunables.services import rule_violations, write_snapshot
from tunables.tags import SYSTEM, log_seed_removed


@dataclass(frozen=True)
class SyncResult:
    created: bool
    rebuilt: bool
    version: int
    violations: Sequence[GroupError | CatalogueValidationError] = ()


def mirror_drift() -> list[str]:
    """Differences between the code and the mirrored categories and seeded tags, one line each."""
    catalogue = get_catalogue()
    drift: list[str] = []
    mirrored = dict(TunableDefinition.objects.filter(is_active=True).values_list("key", "category_name"))
    for group in catalogue.groups.values():
        for tunable in group.tunables:
            key = f"{group.name}.{tunable.name}"
            if key in mirrored and mirrored[key] != group.category:
                drift.append(f"{key}: category {mirrored[key]!r} in the mirror, {group.category!r} in code")
    seeds = _seeds(catalogue)
    seeded = {
        (row.definition.key, row.tag.name)
        for row in TunableDefinitionTag.objects.filter(seeded=True).select_related("definition", "tag")
    }
    drift.extend(f"{key}: seed tag {tag!r} is not assigned" for key, tag in sorted(seeds - seeded))
    drift.extend(f"{key}: assignment of {tag!r} is seeded but not in code" for key, tag in sorted(seeded - seeds))
    seed_names = {tag for _, tag in seeds}
    for name in Tag.objects.filter(from_catalogue=True).exclude(name__in=seed_names).values_list("name", flat=True):
        drift.append(f"{name}: tag is marked as coming from the catalogue but no tunable seeds it")
    return drift


def is_synced() -> bool:
    """True when State exists and its catalogue_version matches the code."""
    return is_current(State.objects.filter(pk=1).first())


def is_current(state: State | None) -> bool:
    """The same check against a State row the caller already has."""
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
    _seed_tags(catalogue)
    if created:
        write_snapshot(catalogue, version=0, changeset=None, created_at=now)
        return SyncResult(created=True, rebuilt=False, version=0, violations=tuple(rule_violations()))
    if state.catalogue_version == catalogue.version:
        return SyncResult(
            created=False, rebuilt=False, version=state.current_version, violations=tuple(rule_violations())
        )
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
    return SyncResult(created=False, rebuilt=True, version=version, violations=tuple(rule_violations()))


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
        "category_name": group.category,
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


def _seeds(catalogue: Catalogue) -> set[tuple[str, str]]:
    """(key, tag) pairs declared in code."""
    return {
        (f"{group.name}.{tunable.name}", tag)
        for group in catalogue.groups.values()
        for tunable in group.tunables
        for tag in tunable.tags
    }


def _seed_tags(catalogue: Catalogue) -> None:
    """Create seed tags, apply seeded assignments, drop seeded assignments no longer in code. Manual rows untouched."""
    seeds = _seeds(catalogue)
    seed_names = {tag for _, tag in seeds}
    for name in seed_names:
        Tag.objects.update_or_create(name=name, defaults={"from_catalogue": True})
    Tag.objects.filter(from_catalogue=True).exclude(name__in=seed_names).update(from_catalogue=False)
    definitions = TunableDefinition.objects.in_bulk([key for key, _ in seeds], field_name="key")
    tags = Tag.objects.in_bulk(seed_names, field_name="name")
    for key, tag in seeds:
        TunableDefinitionTag.objects.update_or_create(
            definition=definitions[key],
            tag=tags[tag],
            defaults={"seeded": True},
            create_defaults={"seeded": True, "assigned_by": SYSTEM},
        )
    for row in TunableDefinitionTag.objects.filter(seeded=True).select_related("definition", "tag"):
        if (row.definition.key, row.tag.name) in seeds:
            continue
        # Only rows this command created carry SYSTEM, so anything else was attached by a caller.
        kept = row.assigned_by != SYSTEM
        if kept:
            row.seeded = False
            row.save(update_fields=["seeded"])
        else:
            row.delete()
        log_seed_removed(row.definition.key, row.tag.name, kept=kept)


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
