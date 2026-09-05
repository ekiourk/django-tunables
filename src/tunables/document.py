from collections.abc import Mapping
from datetime import datetime
from typing import Any

from tunables.catalogue import Catalogue

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
    raise NotImplementedError
