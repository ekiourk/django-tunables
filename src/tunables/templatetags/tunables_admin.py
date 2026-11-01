"""Template tags for the package's admin pages. Load with ``{% load tunables_admin %}``."""

from typing import Any

import django
from django.forms import Media
from django.template import Context, Library
from django.utils.safestring import SafeString

register = Library()

DJANGO_6_1 = django.VERSION >= (6, 1)
BREADCRUMBS_TEMPLATE = "tunables/admin/breadcrumbs_ol.html" if DJANGO_6_1 else "tunables/admin/breadcrumbs_div.html"
STYLESHEETS = ("admin/css/forms.css", "tunables/admin/tunables.css")


@register.simple_tag(takes_context=True)
def tunables_admin_css(context: Context) -> SafeString:
    """Links to the admin's ``forms.css`` and the package's own stylesheet, with the CSP nonce on Django 6.1+."""
    media = Media(css={"all": STYLESHEETS})
    if DJANGO_6_1:
        from django.utils.csp import nonce_attr

        return nonce_attr(context, media=media)
    return media.render()


@register.inclusion_tag(BREADCRUMBS_TEMPLATE, takes_context=True)
def tunables_breadcrumbs(context: Context, steps: list[tuple[str, str]]) -> dict[str, Any]:
    """Home and the app link from ``opts``, then ``steps`` as (label, url); an empty url marks the current page."""
    return {"opts": context["opts"], "steps": steps}
