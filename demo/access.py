"""The EDITABLE_GROUPS hook of the demo: superusers write everything, everyone else stays out of weights."""

from collections.abc import Collection

from django.http import HttpRequest


def editable_groups(request: HttpRequest) -> Collection[str] | None:
    if request.user.is_superuser:
        return None
    return {"pricing", "thermostat"}
