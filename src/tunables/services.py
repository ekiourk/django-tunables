from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from typing import Any

from django.db import transaction
from django.utils import timezone

from tunables.catalogue import Catalogue
from tunables.changes import Actor, Change
from tunables.conf import settings
from tunables.document import FORMAT_VERSION, build_document
from tunables.errors import (
    CatalogueOutOfSync,
    ConstraintError,
    FieldError,
    FieldWarning,
    GroupError,
    NothingToChange,
    UnknownKey,
    UnknownVersion,
    ValidationFailed,
    VersionConflict,
)
from tunables.models import ChangeItem, ChangeSet, Snapshot, State, TunableDefinition, TunableValue
from tunables.publishers import publish
from tunables.registry import get_catalogue


@dataclass(frozen=True)
class ChangeResult:
    version: int
    changeset: ChangeSet
    snapshot: Snapshot
    warnings: Sequence[FieldWarning] = field(default_factory=tuple)


@dataclass(frozen=True)
class _Prepared:
    """A change that survived validation: its key, JSON value or None for a reset, and the old override."""

    key: str
    reset: bool
    new_value: Any
    old_value: Any


def validate(changes: Sequence[Change]) -> list[FieldWarning]:
    """Same checks as apply_changeset without lock or writes. Raises ValidationFailed or NothingToChange."""
    _, warnings = _prepare(get_catalogue(), changes)
    return warnings


@transaction.atomic
def apply_changeset(
    changes: Sequence[Change],
    *,
    actor: Actor,
    source: str,
    reason: str = "",
    expected_version: int | None = None,
    request_id: str = "",
    metadata: Mapping[str, Any] | None = None,
    restores_version: int | None = None,
) -> ChangeResult:
    """Validate and apply changes as one versioned change set with its snapshot."""
    catalogue = get_catalogue()
    state = _locked_state(catalogue)
    if expected_version is not None and expected_version != state.current_version:
        raise VersionConflict(expected_version, state.current_version)
    prepared, warnings = _prepare(catalogue, changes)
    version = state.current_version + 1
    now = timezone.now()
    changeset = ChangeSet.objects.create(
        version=version,
        created_at=now,
        actor=actor.identity,
        actor_source=actor.source,
        client=actor.client,
        reason=reason,
        source=source,
        restores_version=restores_version,
        request_id=request_id,
        catalogue_version=catalogue.version,
        metadata=dict(metadata or {}),
    )
    definitions = TunableDefinition.objects.in_bulk([item.key for item in prepared], field_name="key")
    for item in prepared:
        ChangeItem.objects.create(
            changeset=changeset,
            key=item.key,
            definition=definitions[item.key],
            old_value=item.old_value,
            new_value=item.new_value,
            reset=item.reset,
        )
        if item.reset:
            TunableValue.objects.filter(key=item.key).delete()
        else:
            TunableValue.objects.update_or_create(
                key=item.key,
                defaults={"definition": definitions[item.key], "value": item.new_value, "changeset": changeset},
            )
    snapshot = write_snapshot(catalogue, version=version, changeset=changeset, created_at=now)
    state.current_version = version
    state.save()
    return ChangeResult(version=version, changeset=changeset, snapshot=snapshot, warnings=tuple(warnings))


def rollback(to_version: int, *, actor: Actor, reason: str = "", expected_version: int | None = None) -> ChangeResult:
    """Apply the change set that restores the overrides of snapshot to_version."""
    catalogue = get_catalogue()
    snapshot = Snapshot.objects.filter(version=to_version).first()
    if snapshot is None:
        raise UnknownVersion(to_version)
    document = snapshot.document
    known = set(catalogue.keys())
    target: dict[str, Any] = {}
    for key in document["overridden"]:
        if key in known:
            group, _, name = key.partition(".")
            target[key] = document["groups"][group][name]
    changes: list[Change] = []
    for key, value in target.items():
        tunable = catalogue.get(key)
        if value == tunable.type.to_json(tunable.default):
            changes.append(Change(key, reset=True))
        else:
            changes.append(Change(key, value))
    changes.extend(Change(key, reset=True) for key in stored_overrides(catalogue) if key not in target)
    return apply_changeset(
        changes,
        actor=actor,
        source="rollback",
        reason=reason,
        expected_version=expected_version,
        restores_version=to_version,
    )


def document_changes(document: Mapping[str, Any], *, strict: bool = False) -> tuple[list[Change], list[FieldWarning]]:
    """Changes that set every value in a snapshot document's groups. Unknown keys are skipped, or errors when strict."""
    raise NotImplementedError


def _locked_state(catalogue: Catalogue) -> State:
    state = State.objects.select_for_update().filter(pk=1).first()
    if state is None:
        raise CatalogueOutOfSync("no tunables state; run tunables_sync")
    if state.catalogue_version != catalogue.version:
        raise CatalogueOutOfSync("catalogue changed since the last sync; run tunables_sync")
    return state


@dataclass
class _Report:
    errors: list[FieldError | GroupError] = field(default_factory=list)
    warnings: list[FieldWarning] = field(default_factory=list)


def _prepare(catalogue: Catalogue, changes: Sequence[Change]) -> tuple[list[_Prepared], list[FieldWarning]]:
    overrides = stored_overrides(catalogue)
    report = _Report()
    prepared: list[_Prepared] = []
    seen: set[str] = set()
    for change in changes:
        if change.key in seen:
            report.errors.append(FieldError(change.key, "duplicate", "key appears more than once"))
            continue
        seen.add(change.key)
        item = _prepare_one(catalogue, overrides, change, report)
        if item is not None:
            prepared.append(item)
    report.errors.extend(_group_errors(catalogue, overrides, prepared))
    if report.errors:
        raise ValidationFailed(report.errors)
    if not prepared:
        raise NothingToChange("no effective change")
    return prepared, report.warnings


def _prepare_one(
    catalogue: Catalogue, overrides: Mapping[str, Any], change: Change, report: _Report
) -> _Prepared | None:
    """Coerce and validate one change. Returns None for a no-op or after recording an error."""
    try:
        tunable = catalogue.get(change.key)
    except UnknownKey as error:
        report.errors.append(FieldError(change.key, "unknown_key", str(error)))
        return None
    if tunable.deprecated:
        report.warnings.append(FieldWarning(change.key, "deprecated", f"deprecated: {tunable.deprecated}"))
    old_value = overrides.get(change.key)
    if change.reset:
        return _Prepared(change.key, True, None, old_value) if change.key in overrides else None
    try:
        value = tunable.type.coerce(change.value)
        tunable.type.validate(value)
    except ConstraintError as error:
        report.errors.append(FieldError(change.key, error.code, error.message))
        return None
    new_value = tunable.type.to_json(value)
    current = overrides[change.key] if change.key in overrides else tunable.type.to_json(tunable.default)
    return _Prepared(change.key, False, new_value, old_value) if new_value != current else None


def _group_errors(
    catalogue: Catalogue, overrides: Mapping[str, Any], prepared: Sequence[_Prepared]
) -> list[GroupError]:
    proposed = dict(overrides)
    for item in prepared:
        if item.reset:
            proposed.pop(item.key, None)
        else:
            proposed[item.key] = item.new_value
    touched = {item.key.partition(".")[0] for item in prepared}
    errors: list[GroupError] = []
    for group in (group for group in catalogue.groups.values() if group.name in touched):
        values = {
            tunable.name: tunable.type.coerce(proposed[f"{group.name}.{tunable.name}"])
            if f"{group.name}.{tunable.name}" in proposed
            else tunable.default
            for tunable in group.tunables
        }
        for validator in group.validators:
            try:
                validator(values)
            except ConstraintError as error:
                errors.append(GroupError(group.name, error.code, error.message))
    return errors


def current_version() -> int:
    """Version in the State row. Raises CatalogueOutOfSync when the row is missing."""
    state = State.objects.filter(pk=1).first()
    if state is None:
        raise CatalogueOutOfSync("no tunables state; run tunables_sync")
    return state.current_version


def current_values() -> dict[str, dict[str, Any]]:
    """Effective Python values, defaults overlaid with stored overrides: {group: {name: value}}."""
    catalogue = get_catalogue()
    values = catalogue.defaults()
    for key, raw in stored_overrides(catalogue).items():
        group, _, name = key.partition(".")
        values[group][name] = catalogue.get(key).type.coerce(raw)
    return values


def stored_overrides(catalogue: Catalogue) -> dict[str, Any]:
    """{key: json value} for every override whose key is in the catalogue."""
    known = set(catalogue.keys())
    return {row.key: row.value for row in TunableValue.objects.all() if row.key in known}


def write_snapshot(
    catalogue: Catalogue, *, version: int, changeset: ChangeSet | None, created_at: datetime
) -> Snapshot:
    document = build_document(
        catalogue,
        stored_overrides(catalogue),
        version=version,
        created_at=created_at,
        environment=settings.ENVIRONMENT,
    )
    snapshot = Snapshot.objects.create(
        version=version,
        changeset=changeset,
        created_at=created_at,
        format_version=FORMAT_VERSION,
        catalogue_version=catalogue.version,
        document=document,
    )
    transaction.on_commit(partial(publish, snapshot))
    return snapshot
