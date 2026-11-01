"""Template tags for the package's admin pages. Load with ``{% load tunables_admin %}``."""

from typing import Any

import django
from django.template import Context, Library
from django.templatetags.static import static
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString
from django.utils.translation import gettext as _

register = Library()

DJANGO_6_1 = django.VERSION >= (6, 1)
BREADCRUMBS_TEMPLATE = "tunables/admin/breadcrumbs_ol.html" if DJANGO_6_1 else "tunables/admin/breadcrumbs_div.html"
STYLESHEETS = ("admin/css/forms.css", "tunables/admin/tunables.css")


@register.simple_tag(takes_context=True)
def tunables_admin_css(context: Context) -> SafeString:
    """Links to the admin's ``forms.css`` and the package's own stylesheet, with the CSP nonce on Django 6.1+."""
    nonce: SafeString | str = ""
    if DJANGO_6_1:
        from django.utils.csp import nonce_attr

        if attr := nonce_attr(context):
            nonce = format_html(" {}", attr)
    return format_html_join(
        "\n", '<link rel="stylesheet" href="{}"{}>', ((static(path), nonce) for path in STYLESHEETS)
    )


@register.inclusion_tag(BREADCRUMBS_TEMPLATE, takes_context=True)
def tunables_breadcrumbs(context: Context, steps: list[tuple[str, str]]) -> dict[str, Any]:
    """Home and the app link from ``opts``, then ``steps`` as (label, url); an empty url marks the current page."""
    opts = context["opts"]
    home = [
        (_("Home"), reverse("admin:index")),
        (opts.app_config.verbose_name, reverse("admin:app_list", kwargs={"app_label": opts.app_label})),
    ]
    return {"steps": [*home, *steps]}
