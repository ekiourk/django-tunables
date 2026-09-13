# Changelog

## 0.1.0, 2026-09-13

First release.

- Catalogue declaration with `Integer`, `Float`, `Boolean`, `String`, `Enum` and `List`
  types, custom types, group validators, and a version hash of the structure.
- Change sets with actor, reason, source and request id; append-only history models
  with an optional PostgreSQL trigger guard.
- A snapshot document per version, format version 1, with its JSON Schema shipped as
  package data, a file publisher, and the `snapshot_published` signal.
- REST API for reading groups, definitions, schemas, values, history and snapshots,
  and for writing change sets, group values, rollbacks and imports, with `If-Match`,
  `ETag`, and RFC 9457 problem responses.
- Django admin with a group index, a per-group edit form, read-only history with a
  rollback action, and read-only snapshots.
- Management commands `tunables_sync`, `tunables_show`, `tunables_export`,
  `tunables_import` and `tunables_protect_history`.
