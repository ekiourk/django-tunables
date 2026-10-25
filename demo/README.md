# Demo project

A small Django project that shows django-tunables running: the admin, the API and a shop
settings page with three embedded panels. `scripts/demo_check.sh` drives the same project in
CI against the built wheel, where a template or tag left out of the package fails.

## Run it

```
scripts/demo.sh
```

This runs the `demo_setup` command, which migrates, syncs the catalogue in `demo/catalogue.py`
and creates the users below. Then it serves on port 8000, or on `DEMO_PORT` when set. The
database is `demo/db.sqlite3`, ignored by git. Delete it to start over.

| User | Password | May |
|---|---|---|
| `admin` | `demo` | everything, including the ranking weights |
| `writer` | `demo` | change pricing and thermostat, view the rest |
| `viewer` | `demo` | view values, change nothing |
| `nobody` | `demo` | see the page without any panel |

## What to look at

`/shop/settings/` has three panels. The first is a plain `{% tunables_panel %}` with a required
reason and a full page round trip. The second is rendered with
`demo/templates/demo/panel_htmx.html`, two lines that extend the package template and fill its
`form_attrs` block with the HTMX attributes. Its reason field is hidden, and the change set
records the page instead. The third mixes a pricing key with the weights group, which
`demo/access.py` keeps out of `EDITABLE_GROUPS` for everyone but superusers. For the writer
that panel turns read-only as a whole. Log in as each user and compare. The admin at
`/admin/tunables/` edits whole groups, and `/api/tunables/values/` answers the API.

## The check

`scripts/demo_check.sh` builds the wheel, installs it into a fresh venv next to a copy of this
folder, starts the server and runs `scripts/demo_drive.py` against it. The driver covers what
only a real install can prove: the templates and tags are in the wheel, the extending template
resolves, a save works through real sessions, CSRF tokens and redirects, the error page renders,
an HTMX request gets the demo's fragment back, and the demo's `EDITABLE_GROUPS` hook refuses a
save. `tests/test_panel.py` covers the panel's own behaviour, messages and refusals.
