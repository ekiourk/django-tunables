# Embedding tunables in other pages

A host application often has a page of its own where a few tunables belong: a shop
settings screen that should show the VAT rate, a device page that should let an operator
change the thermostat mode. The embedded panel renders a chosen set of tunables inside
any Django template, as a small form for users who may change them and as a read-only
list for users who may only look. Every save goes through the same change set and
snapshot as the admin and the API, with the same audit trail and version check.

## Setup

Mount the save endpoint once, anywhere in the host's URL conf:

```python
urlpatterns = [
    # ...
    path("tunables/", include("tunables.urls")),
]
```

The repository's [demo project](../demo/README.md) mounts this and shows three panels on
one page. The URL namespace is `tunables_ui`. The panel needs the request in the template context,
which `django.template.context_processors.request` provides. Projects created with
`startproject` have it already. The panel signs its configuration with `SECRET_KEY`.
After a key rotation, a panel already open in a browser stops saving until the page is
reloaded.

## Showing a value

```django
{% load tunables %}

<p>Orders over {% tunable_value "pricing.free_shipping_over" %} EUR ship free.</p>
{% tunable_value "thermostat.mode" as mode %}
```

`tunable_value` returns the effective value of one key from the in-process reader, the
same object `values.get` returns in Python. It performs no permission check: like a value
read in a view, it is part of the page, and the page decides who sees it. An unknown key
raises `UnknownKey` when the template renders.

## The panel

```django
{% load tunables %}

{% tunables_panel "pricing.vat_rate" "pricing.free_shipping_over" "thermostat.mode" title="Shop settings" %}
```

Keys can come from any number of groups. What the panel shows depends on the user:

| User | Panel shows |
|---|---|
| has `tunables.add_changeset`, every key's group is editable, catalogue synced | a form with one field per tunable, a reset box per override, a reason field and a Save button |
| has `tunables.view_tunabledefinition` only, or a group is outside `EDITABLE_GROUPS`, or the catalogue is out of sync | the current values, with an "overridden" marker where a value is not the default, and a notice when the catalogue is out of sync. Out of sync, the values are what the running code serves: its defaults overlaid with the stored overrides it knows, as `values.get` returns them |
| neither permission | nothing |

These are the admin's permissions, see [admin.md](admin.md). `TUNABLES["EDITABLE_GROUPS"]`
receives the host page's request, and a panel that includes a key from a group the
callable does not return falls back to read-only as a whole.

Saving creates a change set with source `panel`, the logged-in user as a verified actor,
and the path of the page in the change set's `metadata` as `page`. The expected version
travels in a hidden field. A value that changed while the page was open is caught the
same way as in the admin: the input is kept, the version is bumped, and a notice names
the keys that moved. Group and catalogue rules run over the whole group, including
tunables the panel does not show.

### Options

| Argument | Default | Meaning |
|---|---|---|
| `reason` | `"required"` | `required` shows a reason field that must be filled. `optional` shows it but accepts it empty. `hidden` shows no field. An empty reason is stored as `Changed from /the/page/path/`, so the history still says where a change came from. |
| `next` | the current page | Where the save returns to. Only URLs on the same host are accepted; anything else falls back to the referer, then to `/`. |
| `title` | `""` | A heading above the panel. |
| `id` | `"tunables-panel"` | The `id` of the panel's `<section>`. Give each panel on a page its own. |
| `template` | `"tunables/panel.html"` | The template that renders the panel. |

### After a save

On success the view redirects to `next` and, when `django.contrib.messages` is
installed, adds the message `Saved version N.`. When the form is invalid, the view
cannot re-render the host page, so it renders a plain standalone page with the panel,
its errors and a link back to `next`. A host that wants errors inside its own page uses
the partial response below.

### Partial responses and HTMX

A POST that carries the `HX-Request` header, or a `partial=1` field, gets the panel's
HTML back instead of a redirect or the standalone page: the form with its errors, the
form with a conflict notice, or a fresh form with a `Saved version N.` line. With HTMX
the override is two attributes on the form, which you add in your own copy of the
template:

```django
<form method="post" action="{{ panel.action_url }}" hx-post="{{ panel.action_url }}" hx-target="#{{ panel.config.id }}" hx-swap="outerHTML">
```

### Styling and overriding the template

The panel has no stylesheet. Its markup is a `<section class="tunables-panel tunables-panel--edit">`
or `tunables-panel--read`, with `tunables-panel__row`, `tunables-panel__reset`,
`tunables-panel__help`, `tunables-panel__errors`, `tunables-panel__notice`,
`tunables-panel__values` and `tunables-panel__overridden` inside, so a host styles it with
its own CSS. To change the markup, copy `tunables/templates/tunables/panel.html` into your
templates, either at the same path to replace it everywhere or under another name passed
as `template=`. The template receives one variable, `panel`, with:

| Attribute | Content |
|---|---|
| `mode` | `edit`, `read` or `hidden` |
| `config` | the options as given to the tag: `keys`, `reason`, `title`, `id`, `template` |
| `rows` | one per key: `tunable`, `field` (the bound form field), `reset` (the bound reset box, or `None` when the value is at its default), `shown` (the value as text with its unit) |
| `form` | the Django form, for `expected_version`, `reason` and non-field errors |
| `token` | the signed panel configuration the form must post back as `panel` |
| `action_url`, `next_url` | the save URL and the return URL |
| `notice` | the conflict or out-of-sync text, or empty |
| `saved_version` | the version just written, in a partial response after a successful save |

The form posts `panel`, `next` and `expected_version` as hidden fields, plus one field
per key named by the full key and a `reset_<key>` box per override. `panel` holds the
keys, reason mode, title, id and template the tag was given, signed with the project's
`SECRET_KEY`. A browser therefore cannot add keys to a panel or drop a reason the page
author required, and an altered token is a `400`. The save view renders the same
template, so a custom template keeps its markup across partial responses and the error
page. A custom template must keep the three hidden fields.

## Building a form in your own view

The form builder behind the panel is available for a view that wants full control:

```python
from django.contrib import messages
from django.shortcuts import redirect, render

from tunables import Actor
from tunables.forms import build_form
from tunables.services import latest_snapshot


def shop_settings(request):
    document = latest_snapshot().document
    form_class = build_form(["pricing.vat_rate", "thermostat.mode"], document, reason="optional")
    form = form_class(request.POST or None)
    if form.is_valid():
        result = form.save(actor=Actor(request.user.get_username(), "verified"), source="panel")
        if result is not None:
            return redirect("shop-settings")
        if form.conflict is not None:
            messages.warning(request, form.conflict_text())
            form = form.rebound()
    return render(request, "shop/settings.html", {"form": form, "rows": form.rows()})
```

`build_form` returns a form class with fields named by full key, a hidden
`expected_version` and a `reason` field in the requested mode. `save()` applies the
changes as one change set with the reason entered in the form, or the `reason` argument
when the field is empty, and returns the result, or `None` when nothing was written.
Validation errors and "nothing changed" land on the form. A version conflict is kept in
`form.conflict`; `conflict_text()` describes it and `rebound()` returns the same input
bound against the current version. `changes()` returns the change objects and `rows()`
the row objects the panel template uses. The view must apply the permission checks
itself; `tunables.access.check_keys_editable` applies `EDITABLE_GROUPS` to a list of
keys.

## Rendering a widget in JavaScript

A client-side widget needs no package support beyond the API. Fetch the schema of the
tunables you show, fetch their values, render with a JSON Schema form library, and post
a change set:

```
GET  /api/tunables/schema/?key=pricing.vat_rate&key=thermostat.mode
GET  /api/tunables/values/?key=pricing.vat_rate&key=thermostat.mode
POST /api/tunables/changesets/   with If-Match: "<version>" from the values response
```

See [api.md](api.md) for the `key` filter and the write endpoints.
