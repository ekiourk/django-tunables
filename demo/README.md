# Demo project

A small Django project that shows django-tunables running: the admin, the API and a shop
settings page with three embedded panels. It is also what `scripts/demo_check.sh` drives in
CI, against the built wheel, so a template or tag left out of the package fails there.

## Run it

```
scripts/demo.sh
```

This migrates, syncs the catalogue in `demo/catalogue.py`, creates the users below and serves
on port 8000 (`DEMO_PORT` picks another). The database is `demo/db.sqlite3`, ignored by git;
delete it to start over.

| User | Password | May |
|---|---|---|
| `admin` | `demo` | everything, including the ranking weights |
| `writer` | `demo` | change pricing and thermostat, view the rest |
| `viewer` | `demo` | view values, change nothing |
| `nobody` | `demo` | see the page without any panel |

## What to look at

`/shop/settings/` has three panels. The first is a plain `{% tunables_panel %}` with a required
reason and a full page round trip. The second is rendered with the demo's copy of the panel
template, `demo/templates/demo/panel_htmx.html`, two lines that extend the package template
and fill its `form_attrs` block with the HTMX attributes, and its
reason field is hidden, so the change set records the page instead. The third mixes a pricing
key with the weights group, which `demo/access.py` keeps out of `EDITABLE_GROUPS` for everyone
but superusers, so it turns read-only as a whole for the writer. Log in as each user and
compare. The admin at `/admin/tunables/` edits whole groups, and `/api/tunables/values/`
answers the API.

## The check

`scripts/demo_check.sh` builds the wheel, installs it into a fresh venv next to a copy of this
folder, starts the server and runs `scripts/demo_drive.py` against it. The driver logs in as
each user and walks the panels with real sessions, CSRF tokens and redirects: a save, an empty
reason, a stale version, an HTMX swap, an invalid value, a refused group and an altered token.
