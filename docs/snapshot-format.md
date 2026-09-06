# Snapshot format

Every version of the tunables produces one snapshot: a JSON document holding the
complete effective parameter set. Readers are separate programs. They depend on this
document and on the two tables described below, and never on the Python package.

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
  the time of the snapshot is present, whether or not it was ever changed. A reader never
  needs the catalogue to know what exists.
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

## Schema

The package ships a JSON Schema (draft 2020-12) of the document at
`tunables/schemas/snapshot-v1.schema.json`. Copy it into a reader to validate documents
in tests. The schema constrains the top-level fields, the `catalogue_version` pattern,
and the uniqueness of `overridden`. It does not know the catalogue, so it does not
constrain which groups and tunables exist or what their value types are.

## Reader contract: database tables

Readers that share the database poll one table and fetch from another.

`tunables_state`, exactly one row:

| Column | Type | Meaning |
|---|---|---|
| `current_version` | integer | The version of the latest snapshot. |

`tunables_snapshot`, one row per version:

| Column | Type | Meaning |
|---|---|---|
| `version` | integer, unique | Snapshot version. |
| `document` | JSON | The document above. |
| `created_at` | timestamp with time zone | Same instant as `created_at` inside the document. |

The intended loop is: read `current_version` cheaply on an interval, and when it
differs from the last version seen, fetch the row of `tunables_snapshot` with that
`version` and replace the in-memory configuration atomically.

These table and column names are stable across releases of the package. Renaming them
is a major version change.

Other columns exist on both tables for the package's own use and may change between
minor releases. Readers should select only the columns named here.
