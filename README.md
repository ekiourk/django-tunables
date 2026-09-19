# django-tunables

Runtime-tunable parameters for Django with a full audit trail. The host project declares
a catalogue of parameters in Python: keys, types, defaults, constraints, grouping.
Operators change values through the Django admin or a REST API. Every change is an
atomic, versioned change set, and every version produces a snapshot: one JSON document
holding the complete effective parameter set, ready for other processes to read from
the database or a file. Each group can also be described as JSON Schema plus a UI
schema, so an external client can render its own editing forms.

It is not a replacement for Django settings, and it does not patch `django.conf.settings`.
It has no per-user, per-tenant or per-environment scopes; one database holds one
catalogue with one effective value per key. Scheduled changes, nullable values, and
notifications beyond the publisher hook are out of scope.

## How it differs from django-constance and django-dynamic-preferences

Both of those store dynamic settings in the database and edit them in the admin.
django-tunables adds change sets with actor and reason, an append-only history, a
snapshot per version that other processes can read without importing the package, and a
schema endpoint that describes each group to external clients. Two ideas were borrowed:
the type to form field mapping comes from constance, and giving each type its own form
field, serializer and validator comes from dynamic-preferences.

## Requirements

Python 3.12 or newer, Django 5.2 or newer, Django REST Framework 3.15 or newer.
PostgreSQL 14 or newer for the optional database-level history protection. SQLite works
for everything else.

```
pip install django-tunables
```

## Quickstart

Add the apps and point the setting at your catalogue. In `settings.py`:

<!-- quickstart: settings -->
```python
INSTALLED_APPS = [
    # ...
    "rest_framework",
    "tunables",
]

TUNABLES = {"CATALOGUE": "myproject.tunables_catalogue.catalogue"}
```

Declare the catalogue. In `myproject/tunables_catalogue.py`:

<!-- quickstart: catalogue -->
```python
from tunables import Boolean, Catalogue, Enum, Float, Group, List, Tunable

pricing = Group(
    "pricing",
    title="Pricing",
    order=1,
    tunables=[
        Tunable("vat_rate", Float(min=0.0, max=1.0), 0.24, title="VAT rate"),
        Tunable("free_shipping_over", Float(min=0.0), 50.0, title="Free shipping over", unit="EUR"),
        Tunable("currencies", List(Enum(["EUR", "USD", "GBP"]), min_items=1, unique=True), ["EUR"]),
        Tunable("allow_backorders", Boolean(), False),
    ],
)

thermostat = Group(
    "thermostat",
    title="Thermostat",
    order=2,
    tunables=[
        Tunable("target_c", Float(min=5.0, max=30.0), 21.0, title="Target temperature", unit="°C"),
        Tunable("mode", Enum(["auto", "heat", "cool", "off"]), "auto"),
    ],
)

catalogue = Catalogue([pricing, thermostat])
```

Mount the API next to the admin. In `myproject/urls.py`:

<!-- quickstart: urls -->
```python
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/tunables/", include("tunables.api.urls")),
]
```

Create the tables and the first snapshot:

<!-- quickstart: setup -->
```
python manage.py migrate
python manage.py tunables_sync
```

Read the effective values:

<!-- quickstart: read -->
```
curl -s http://localhost:8000/api/tunables/values/
```

Change one:

<!-- quickstart: change -->
```
curl -s -X POST http://localhost:8000/api/tunables/changesets/ \
  -H "Content-Type: application/json" \
  -H "X-Tunables-Actor: alice" \
  -d '{"changes": [{"key": "pricing.vat_rate", "value": 0.2}], "reason": "autumn rate"}'
```

The response is the new change set with version 1. `tunables_show` prints the effective
values with an override marker, and the admin's Tunables page offers the same edit as a
form. Run `tunables_sync` after every deployment, after `migrate`, so the database
mirror follows the catalogue in code.

## Settings

All settings live in one dictionary, `TUNABLES`. Only `CATALOGUE` is required.

| Key | Default | Meaning |
|---|---|---|
| `CATALOGUE` | required | Dotted path to a `Catalogue` instance or to a zero-argument callable returning one. |
| `ENVIRONMENT` | `""` | Written into every snapshot document as `environment`. |
| `PUBLISHERS` | `[]` | Dotted paths of publisher classes, instantiated once with no arguments. |
| `FILE_PUBLISHER_PATH` | `None` | Target file of `tunables.publishers.FilePublisher`. |
| `PAGE_SIZE` | `50` | Page size of the change set list endpoint. |
| `READ_CACHE_TTL` | `1.0` | Seconds the in-process reader may serve values without checking the stored version. `0` checks on every read, `None` never checks. |
| `API_AUTHENTICATION_CLASSES` | `None` | DRF authentication classes for the API. `None` uses the host's DRF defaults. |
| `API_PERMISSION_CLASSES` | `None` | DRF permission classes for the API. `None` uses the host's DRF defaults. |
| `ACTOR_RESOLVER` | `tunables.api.actors.default_actor_resolver` | Callable turning a request into an `Actor`. |
| `EDITABLE_GROUPS` | `None` | Callable returning the group names a request may write, or `None` for all. Applied by the API and the admin. |
| `ACTOR_HEADER` | `X-Tunables-Actor` | Header naming the actor of an unauthenticated request. |
| `CLIENT_HEADER` | `X-Tunables-Client` | Header naming the client program. |
| `REQUEST_ID_HEADER` | `X-Request-ID` | Header whose value is recorded as the change set's request id. |
| `REASON_HEADER` | `X-Tunables-Reason` | Header carrying the reason for a `PATCH` of group values. |

## Management commands

| Command | Does |
|---|---|
| `tunables_sync [--check]` | Mirrors the catalogue into the database, creates the state row and snapshot 0 on a fresh database, and writes a system version when the catalogue structure changed. `--check` exits 1 when the stored catalogue version differs from the code or when mirrored categories and seeded tags differ from the code, without writing. Idempotent. Both forms print a warning per group or catalogue rule the stored values break; the exit code does not change for that. |
| `tunables_show [--group NAME] [--json]` | Prints the effective values of the latest snapshot, one key per line, marking overrides. `--json` prints the snapshot document. |
| `tunables_export [--output FILE] [--defaults [--created-at ISO8601]] [--schema]` | Writes the latest snapshot document as JSON to a file or to standard output. `--defaults` writes the version-zero document from the catalogue in code, `--schema` writes the JSON Schema of documents for that catalogue; both need no database. `--created-at` fixes the timestamp of the defaults document so repeated runs produce the same file. |
| `tunables_import FILE --actor NAME [--reason TEXT] [--strict] [--replace]` | Applies the values of a snapshot document as one change set with `source: import`. Unknown keys are skipped with a warning, or rejected with `--strict`. `--replace` also resets every override the document does not name, so the document becomes the complete state. |
| `tunables_protect_history [--remove] [--database ALIAS]` | Installs PostgreSQL triggers that reject `UPDATE`, `DELETE` and `TRUNCATE` on the history tables. |
| `tunables_publish [VERSION] [--publisher PATH]` | Sends a stored snapshot, the latest by default, to the configured publishers again and records the outcome. Fails when a publisher fails. |

`tunables_sync --check` compares the catalogue hash, the mirrored categories and the
seeded tags. A change to a title or description makes the mirror stale without failing
the check; the next `tunables_sync` updates the mirror without writing a new version.
The API and the admin answer `503` on the hash alone, so stale categories or tags never
take the API down.

## Publishers and the signal

The snapshot row in the database is always written. In addition, every committed
snapshot is sent to the `tunables.signals.snapshot_published` signal, with the
`Snapshot` instance as `snapshot`, and then to each publisher named in `PUBLISHERS`.
A publisher is any object with a `publish(snapshot)` method. An exception inside
`publish` is logged under the `tunables.publishers` logger and does not affect the
write. Both the signal and the publishers run after the transaction commits.

Each publisher's outcome is recorded: the version it last received, when, and the last
error if any. `GET status/` shows this per publisher, so a gap between the current version
and a publisher's last version is visible. `manage.py tunables_publish [VERSION]
[--publisher PATH]` sends a stored snapshot, the latest by default, to all or one
publisher again, and fails with the publisher's error when it does not succeed.

`tunables.publishers.FilePublisher` writes the document to `FILE_PUBLISHER_PATH`, using
a temporary file in the same directory and a rename, so readers never see a partial
file:

```python
TUNABLES = {
    "CATALOGUE": "myproject.tunables_catalogue.catalogue",
    "PUBLISHERS": ["tunables.publishers.FilePublisher"],
    "FILE_PUBLISHER_PATH": "/var/lib/myproject/tunables.json",
}
```

## Reading values in Django code

Code inside the project reads its tunables through one object, which caches the whole
value set in the process:

```python
from tunables import values

rate = values.get("pricing.vat_rate")
pricing = values.group("pricing")
```

A read serves from memory. At most once per `READ_CACHE_TTL` seconds it checks the
stored version with one small query and reloads when the version moved, and a write in
the same process drops the cache at once. See [docs/reading.md](docs/reading.md).

## Reading snapshots from other processes

A reader polls `tunables_state.current_version`, and when it changes fetches
`tunables_snapshot.document` for that version. The document format, the JSON Schema
shipped with the package, and the table contract are in
[docs/snapshot-format.md](docs/snapshot-format.md).

## API

| Method and path | Purpose |
|---|---|
| `GET categories/`, `GET groups/`, `GET groups/{group}/` | categories, groups and their definitions |
| `GET definitions/`, `GET tags/`, `GET tags/{name}/` | every tunable with its type, default, texts and tags; filters by category, group, tag and text |
| `GET groups/{group}/schema/`, `GET schema/` | JSON Schema and UI schema per group, optionally narrowed to the tunables carrying given tags |
| `GET snapshots/schema/` | JSON Schema of snapshot documents for this catalogue |
| `GET values/`, `GET groups/{group}/values/` | effective values, with `ETag` |
| `GET changesets/`, `GET changesets/{version}/` | history, paginated and filterable |
| `GET snapshots/latest/`, `GET snapshots/{version}/`, `GET export/` | snapshot documents |
| `GET diff/?from=&to=` | per-key changes between two versions |
| `GET status/` | sync state, current version and catalogue hashes, for probes |
| `POST changesets/`, `POST validate/` | apply or dry-run a list of changes |
| `PATCH groups/{group}/values/` | form-shaped write of one group |
| `POST rollback/`, `POST import/` | restore a version, import a document |
| `POST tags/`, `PATCH`/`DELETE tags/{name}/`, `PUT definitions/{key}/tags/` | manage tags and their assignments |

Writes accept `If-Match` for optimistic concurrency and every response carries
`X-Tunables-Version`. Errors are RFC 9457 problem documents. The full reference,
including the problem types and error codes, is in [docs/api.md](docs/api.md).

## Admin

The Tunables page in the admin lists the groups by category and edits one group per
form with a reason field and a reset box per override. A definitions page browses every
tunable by category, group, tag and text, and tags can be edited per definition. The
change set history is read only and has a rollback action. See [docs/admin.md](docs/admin.md).

A screenshot of the group edit form will be added here.

## Documentation

- [Declaring the catalogue](docs/catalogue.md)
- [Reading values in Django code](docs/reading.md)
- [REST API](docs/api.md)
- [Django admin](docs/admin.md)
- [Snapshot format and reader contract](docs/snapshot-format.md)

## Development

```
uv sync --all-extras
uv run pytest
uv run pytest --postgres    # the whole suite on a PostgreSQL testcontainer, needs Docker
uv run ruff check . && uv run mypy src
scripts/quickstart_check.sh    # installs the built wheel into a fresh project and runs the quickstart
```

## License

MIT. See [LICENSE](LICENSE).
