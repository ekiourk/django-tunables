# Snapshot format

Every version of the tunables produces one snapshot: a JSON document holding the
complete effective parameter set. Readers are separate programs that depend on this
document and on the two tables described below. They do not import the Python package.

The format is versioned by `format_version`. Any change to the shape of the document
is a new format version. This page describes format version 1.

## Document

```json
{
  "format_version": 1,
  "version": 42,
  "created_at": "2026-09-05T09:53:19.123456+00:00",
  "catalogue_version": "sha256:3b1f…",
  "environment": "production",
  "groups": {
    "pricing": {
      "vat_rate": 0.24,
      "free_shipping_over": 50.0,
      "currencies": ["EUR", "USD"]
    },
    "thermostat": {
      "target_c": 21.5,
      "mode": "auto"
    }
  },
  "overridden": ["pricing.vat_rate", "thermostat.mode"]
}
```

| Field | Type | Meaning |
|---|---|---|
| `format_version` | integer, always `1` | Shape of this document. |
| `version` | integer, from 0 | The tunables version this snapshot materializes. Strictly increasing. Version 0 is all defaults. |
| `created_at` | string, RFC 3339 with offset | When the snapshot was produced. |
| `catalogue_version` | string, `sha256:` and 64 hex digits | Hash of the catalogue structure the document was built from. Changes when a tunable is added, removed, renamed, retyped, or its default changes. Does not change on wording. |
| `environment` | string, may be empty | The `TUNABLES["ENVIRONMENT"]` setting of the producing host. |
| `groups` | object of objects | Every group and every tunable in the catalogue, with its effective value. |
| `overridden` | array of strings, sorted, unique | Full keys `group.name` that have a stored override rather than the default. |

Rules readers can rely on:

- `groups` is complete. Every group and every tunable that exists in the catalogue at
  the time of the snapshot is present, whether or not it was ever changed, so a reader
  can learn what exists from the document alone.
- A tunable removed from the catalogue is absent from the next snapshot. A tunable added
  to the catalogue appears with its default.
- Values are the JSON representation of the tunable's type. The built-in types produce
  JSON numbers, booleans, strings, and arrays. A reader that wants a specific Python or
  host type converts from JSON itself.
- Group and tunable names are stable identifiers matching `^[a-z][a-z0-9_]*$`. Titles and
  descriptions are not in the document.
- The document is a plain JSON object with no additional top-level keys.
- Key order inside objects is not significant. Databases that store the document as
  binary JSON return keys in their own order. Readers must look keys up by name.

## Schemas

Two JSON Schemas (draft 2020-12) describe the document.

The envelope schema ships with the package at `tunables/schemas/snapshot-v1.schema.json`.
It constrains the top-level fields, the `catalogue_version` pattern, and the uniqueness
of `overridden`. It does not know the catalogue, so it does not constrain which groups
and tunables exist or what their value types are. It is the same for every host.

The catalogue schema is generated from a host's catalogue. It has the same envelope,
but `catalogue_version` is fixed to that catalogue's hash, `groups` names every group
and every tunable as a required, typed property with its bounds and choices, and
`overridden` only admits keys that exist. A document from a different catalogue fails
against it. Obtain it with `manage.py tunables_export --schema`, which needs no
database, or from `GET snapshots/schema/` on the API, which answers only while the
deployment is in sync. Its `$id` is `urn:tunables:snapshot:v1:<catalogue hash>`, so a
reader can check that the schema it holds matches the documents it receives by
comparing the hash with `catalogue_version`. Its top-level `x-validators` lists the
catalogue-level rules that the server enforces and the schema cannot express. Each
tunable property carries `x-tags`: from the API, the tag names stored for it; from the
export command, always `[]`, since the command reads no database.

## The version-zero document without a database

A reader that starts before any snapshot is reachable needs the all-defaults document.
`manage.py tunables_export --defaults` writes it from the catalogue in code, with
`version` 0, an empty `overridden`, the catalogue hash and the host's `environment`,
and needs neither a database nor `tunables_sync`. The same document is available in
Python as `tunables.document.defaults_document(catalogue, environment="production")`,
where `environment` defaults to the empty string.

Both that function and `tunables.schema.document_schema(catalogue)` work with no Django
settings configured, so a build script in a contracts package can write the committed
artifacts from the catalogue alone:

```python
from tunables.document import defaults_document
from tunables.schema import document_schema

Path("defaults.json").write_text(json.dumps(defaults_document(catalogue), indent=2))
Path("snapshot.schema.json").write_text(json.dumps(document_schema(catalogue), indent=2))
```

Generating them through `manage.py tunables_export` needs a Django project, and reads
`TUNABLES["ENVIRONMENT"]` for you.

The output equals the snapshot 0 that `tunables_sync` writes on a fresh database with
the same catalogue and environment, except for `created_at`, which is the time of the
run. A committed defaults file should use a fixed timestamp, so that a drift test can
compare the file byte for byte: `tunables_export --defaults --created-at
2026-09-01T00:00:00+00:00` stamps that instant instead of now, and
`defaults_document(catalogue, created_at=...)` does the same in Python. The value must
carry a UTC offset.

## Reader contract: database tables

Readers that share the database poll one table and fetch from another.

`tunables_state`, exactly one row:

| Column | Type | Meaning |
|---|---|---|
| `current_version` | integer | The version of the latest snapshot. |

`tunables_snapshot`, one row per version that has not been pruned:

| Column | Type | Meaning |
|---|---|---|
| `version` | integer, unique | Snapshot version. |
| `document` | JSON | The document above. |
| `created_at` | timestamp with time zone | Same instant as `created_at` inside the document. |

## Retention

Each version writes a full document holding every tunable, so the table grows by the
size of the whole parameter set on every change.
`manage.py tunables_prune_snapshots --keep 200` keeps the newest 200 documents and
removes the rest. `--before 2026-01-01T00:00:00+00:00` removes by age instead, and
`--dry-run` reports without deleting.

Deletion runs in batches of 500 versions per statement, which `--batch-size` changes. A
first prune on a table that has grown for years therefore avoids one enormous
transaction.

Every prune that deletes something writes an info line to the `tunables.services` logger
naming the policy and the versions removed. Grep for it when a version has gone missing.

Version 0 and the current version always stay, whatever the policy asks for. Version 0
is the all-defaults baseline and the current one is what every reader polls for. Change
sets and change items are never touched, so the audit trail stays complete and a pruned
document can be rebuilt from it.

A pruned version is gone from the API: `GET snapshots/{version}/` answers `404`,
`GET diff/` with it on either side answers `404`, and a rollback to it answers
`422 unknown-version`. Keep enough history to cover the rollbacks and comparisons your
operators actually reach for.

`tunables_protect_history` reflects the same distinction. Its triggers reject every
`UPDATE` and `TRUNCATE`, and they reject `DELETE` on the change sets and items. On the
snapshot table `DELETE` is allowed, so retention runs on a protected deployment without
lifting the protection first.

An exported document can also be imported into another deployment with the same
catalogue, through `tunables_import` or `POST import/`. In replace mode the target ends
up with exactly the overrides of the document, which is how a tuned state is promoted
from one environment to another.

The intended loop is: read `current_version` cheaply on an interval, and when it
differs from the last version seen, fetch the row of `tunables_snapshot` with that
`version` and replace the in-memory configuration atomically.

These table and column names are stable across releases of the package. Renaming them
is a major version change.

Other columns exist on both tables for the package's own use and may change between
minor releases. Readers should select only the columns named here.
