from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class Change:
    key: str
    value: Any = None
    reset: bool = False


@dataclass(frozen=True)
class Actor:
    identity: str
    source: Literal["verified", "asserted", "system"]
    client: str = ""
