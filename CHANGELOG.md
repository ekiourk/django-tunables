# Changelog

## Unreleased

- `tunables_export --defaults` accepts `--created-at` with an ISO 8601 datetime and
  offset, so a committed defaults file can use a fixed timestamp and a drift test can
  compare it byte for byte. A naive value, or the flag without `--defaults`, is an error.
- `metadata` and `ui` on a `Tunable` or `Group` must be JSON serialisable. A value that
  is not, such as a set or NaN, is a `CatalogueError` at construction instead of a
  failure at `tunables_sync`.
- `POST changesets/` accepts an optional `metadata` object, stored on the change set as
  given and returned in the change set list and detail. A non-object is a `400`.
- Rules the stored values break after a catalogue change are reported instead of
  discovered on the next write. `services.rule_violations()` returns them, `sync()`
  returns them on `SyncResult.violations`, `tunables_sync` prints them as warnings on
  standard error without changing its exit code, `GET status/` lists them as
  `rule_violations`, and the admin group index shows them above the table.

## 0.1.1, 2026-09-18

- `tunables_sync` now resets overrides that no longer coerce or validate after a
  catalogue change, recording each as a reset item of the system change set. Before,
  a retyped or narrowed tunable left its old value in the snapshot, and reading or
  writing that group raised `TypeCoercionError`.
- A stored value that cannot be coerced is reported as a group validation error
  instead of escaping from `apply_changeset` and `validate`.
- Writing a value equal to the tunable's default removes the override when one exists
  and is a no-op otherwise, so `overridden` never lists a key holding its default. The
  admin, the API and import all follow this rule.
- Dry runs check `If-Match` and answer `412` on a mismatch, as the documentation said.
- New setting `REASON_HEADER`, default `X-Tunables-Reason`, names the header that carries
  the reason for `PATCH groups/{group}/values/`.
- `tunables_sync` locks the state row before mirroring the catalogue, so concurrent
  syncs run one at a time.

## 0.1.0, 2026-09-18

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
