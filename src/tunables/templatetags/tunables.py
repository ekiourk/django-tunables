"""Template tags for showing and editing tunables inside a host page. Load with ``{% load tunables %}``."""

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.template import Context, Library
from django.template.loader import render_to_string
from django.utils.safestring import SafeString

from tunables.forms import ReasonMode
from tunables.panel import PANEL_TEMPLATE, PanelConfig, build_panel
from tunables.reader import values

register = Library()


@register.simple_tag
def tunable_value(key: str) -> Any:
    """The effective value of one full key, from the in-process reader. ``{% tunable_value "pricing.vat_rate" %}``."""
    return values.get(key)


@register.simple_tag(takes_context=True)
def tunables_panel(
    context: Context,
    *keys: str,
    reason: ReasonMode = "required",
    next: str = "",  # noqa: A002
    title: str = "",
    id: str = "tunables-panel",  # noqa: A002
    template: str = PANEL_TEMPLATE,
) -> SafeString:
    """A form over ``keys`` for users who may write them, a read-only list for users who may view, else nothing.

    ``reason`` is ``required``, ``optional`` or ``hidden``; an empty reason is filled with the page the change
    came from. ``next`` is where the save returns to, by default the current page. ``template`` overrides the
    panel template. Needs ``request`` in the context, from ``django.template.context_processors.request``.
    """
    request = context.get("request")
    if request is None:
        raise ImproperlyConfigured(
            "tunables_panel needs the request in the template context; "
            "add django.template.context_processors.request to your template settings"
        )
    panel = build_panel(request, PanelConfig(keys, reason, title, id), next_url=next)
    return render_to_string(template, {"panel": panel}, request=request)
