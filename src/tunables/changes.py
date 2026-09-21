from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class Change:
    key: str
    value: Any = None
    reset: bool = False


FIELD_WIDTH = 255


@dataclass(frozen=True)
class Actor:
    identity: str
    source: Literal["verified", "asserted", "system"]
    client: str = ""

    def __post_init__(self) -> None:
        # The columns holding these are 255 wide, and a header can be any length.
        object.__setattr__(self, "identity", self.identity[:FIELD_WIDTH])
        object.__setattr__(self, "client", self.client[:FIELD_WIDTH])
