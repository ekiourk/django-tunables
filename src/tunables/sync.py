from dataclasses import dataclass


@dataclass(frozen=True)
class SyncResult:
    created: bool
    rebuilt: bool
    version: int


def sync() -> SyncResult:
    """Mirror the catalogue, bootstrap State and snapshot 0, rebuild on catalogue change. Idempotent."""
    raise NotImplementedError


def is_synced() -> bool:
    """True when State exists and its catalogue_version matches the code."""
    raise NotImplementedError
