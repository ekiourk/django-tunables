"""The EDITABLE_GROUPS hook of the demo: superusers write everything, everyone else stays out of weights."""

from collections.abc import Collection

from django.http import HttpRequest

from tunables.registry import get_catalogue

RESTRICTED = {"weights"}


def editable_groups(request: HttpRequest) -> Collection[str] | None:
    if request.user.is_superuser:
        return None
    return {name for name in get_catalogue().groups if name not in RESTRICTED}
