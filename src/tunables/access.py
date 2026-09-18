from collections.abc import Collection, Sequence
from typing import Any

from django.utils.module_loading import import_string

from tunables.changes import Change
from tunables.conf import settings
from tunables.errors import GroupNotEditable


def editable_groups(request: Any) -> Collection[str] | None:
    """The group names this request may write, from TUNABLES["EDITABLE_GROUPS"]. None means every group."""
    hook = settings.EDITABLE_GROUPS
    if hook is None:
        return None
    if isinstance(hook, str):
        hook = import_string(hook)
    editable: Collection[str] | None = hook(request)
    return editable


def check_editable(request: Any, changes: Sequence[Change]) -> None:
    """Raise GroupNotEditable for the first change whose group the request may not write."""
    editable = editable_groups(request)
    if editable is None:
        return
    for change in changes:
        group = change.key.partition(".")[0]
        if group not in editable:
            raise GroupNotEditable(group)
