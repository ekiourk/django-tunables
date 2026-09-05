from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tunables.catalogue import Catalogue
from tunables.changes import Actor, Change
from tunables.conf import settings
from tunables.document import FORMAT_VERSION, build_document
from tunables.errors import CatalogueOutOfSync, FieldWarning
from tunables.models import ChangeSet, Snapshot, State, TunableValue
from tunables.registry import get_catalogue


@dataclass(frozen=True)
class ChangeResult:
    version: int
    changeset: ChangeSet
    snapshot: Snapshot
    warnings: Sequence[FieldWarning] = field(default_factory=tuple)


def validate(changes: Sequence[Change]) -> list[FieldWarning]:
    """Same checks as apply_changeset without lock or writes. Raises ValidationFailed or NothingToChange."""
    raise NotImplementedError


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
    raise NotImplementedError


def rollback(to_version: int, *, actor: Actor, reason: str = "", expected_version: int | None = None) -> ChangeResult:
    """Apply the change set that restores the overrides of snapshot to_version."""
    raise NotImplementedError


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
    return Snapshot.objects.create(
        version=version,
        changeset=changeset,
        created_at=created_at,
        format_version=FORMAT_VERSION,
        catalogue_version=catalogue.version,
        document=document,
    )
