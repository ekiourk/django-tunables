from collections.abc import Mapping
from datetime import datetime
from typing import Any

from django.utils import timezone

from tunables.catalogue import Catalogue
from tunables.conf import settings

FORMAT_VERSION = 1


def build_document(
    catalogue: Catalogue,
    overrides: Mapping[str, Any],
    *,
    version: int,
    created_at: datetime,
    environment: str = "",
) -> dict[str, Any]:
    """Snapshot document v1: every group and tunable with its effective JSON value."""
    if created_at.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")
    groups: dict[str, dict[str, Any]] = {}
    overridden: list[str] = []
    for group in catalogue.groups.values():
        values: dict[str, Any] = {}
        for tunable in group.tunables:
            key = f"{group.name}.{tunable.name}"
            if key in overrides:
                values[tunable.name] = overrides[key]
                overridden.append(key)
            else:
                values[tunable.name] = tunable.type.to_json(tunable.default)
        groups[group.name] = values
    return {
        "format_version": FORMAT_VERSION,
        "version": version,
        "created_at": created_at.isoformat(),
        "catalogue_version": catalogue.version,
        "environment": environment,
        "groups": groups,
        "overridden": sorted(overridden),
    }


def defaults_document(catalogue: Catalogue, *, created_at: datetime | None = None) -> dict[str, Any]:
    """The version-zero document from the catalogue alone: every default, nothing overridden."""
    return build_document(
        catalogue,
        {},
        version=0,
        created_at=created_at or timezone.now(),
        environment=settings.ENVIRONMENT,
    )
