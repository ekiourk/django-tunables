# Changelog

## Unreleased

- `from tunables import values` reads the effective values inside the Django project:
  `values.get("pricing.vat_rate")`, `values.group("pricing")` and `values.all()`, all
  coerced to the tunable's Python type. Each process caches the whole set and checks the
  stored version at most once per `READ_CACHE_TTL` seconds, one second by default. A
  write in the process drops its cache at once. Before the first sync the reader serves
  the defaults from code instead of raising.
- New setting `READ_CACHE_TTL`.
- A version conflict in the admin keeps what the operator typed. The form comes back
  with their own values, the hidden version updated to the current one, and a message
  naming the tunables another change set moved in the meantime.
- New `tunables.publishers.QueuedPublisher`, a base class for publishers that hand the
  version to a queue instead of sending the snapshot inline. Publishers run inside the
  request that made the change, so anything doing network work belongs on a queue.
- New page on deploying a catalogue change: what the structure hash covers, which
  surfaces answer `503` in the window before `tunables_sync`, and how to order the steps
  of a rolling deployment.
- `GET groups/{group}/schema/` and `GET schema/` take a repeatable `tag` parameter and
  describe only the tunables carrying every named tag. `properties` and the UI controls
  shrink to those tunables, a section left without controls is dropped, and `$id`,
  `x-validators` and `additionalProperties` stay as they are. A group with no matching
  tunable is `404` on the group endpoint and omitted from `schema/`. Matching reads the
  database, so a manual tag assignment takes effect without `tunables_sync`.
- Every property of a group schema carries `x-tags`, the stored tag names of the tunable.
  The snapshot schema from the API has them too; `tunables_export --schema` writes `[]`.
- A `tag` query value that names no stored tag is `422 unknown-tag`, with the first
  unknown name in `tag`. This also applies to `GET definitions/?tag=`, which used to
  match nothing.

## 0.3.0, 2026-09-19

Categories group the groups, tags label tunables across groups, and the admin and API
can browse by both.

Changes to existing behaviour:

- `Catalogue` takes `label` and `validators` as keyword-only arguments, alongside the
  new `categories`. A caller that passed `label` positionally must name it.
- Groups are ordered by their category first, then by `(order, name)`. A catalogue that
  declares no categories keeps its order, since every group is in the implicit
  `general`. The version hash still walks groups in `(order, name)` order, so moving a
  group between categories writes no new version.
- `tunables_sync --check` exits 1 when the mirrored categories or seeded tags differ
  from the code. The API and the admin keep answering on the catalogue hash alone.

Additions:

- `Category(name, title, description, order)`; `Group(category=...)`; `Tunable(tags=...)`
  with seed tags matching `^[a-z0-9][a-z0-9_-]*$`; `Catalogue.categories` and
  `Catalogue.groups_in()`. Categories and tags are outside the version hash.
- Models `Tag` and `TunableDefinitionTag`, `TunableDefinition.category_name`, migration
  `0003`. `tunables_sync` mirrors categories and seeds tags, leaving manual tags and
  assignments alone.
- API reads: `GET categories/`; `category` on groups and definitions; `groups/?category=`;
  `definitions/?category=&group=&tag=&q=`; `GET tags/` and `GET tags/{name}/`;
  `x-category` on group schemas. Tag reads work while the catalogue is out of sync.
- API writes: `POST tags/`, `PATCH` and `DELETE tags/{name}/`,
  `PUT definitions/{key}/tags/`, with problem types `tag-exists` and `tag-seeded`. Tag
  writes create no change set and record no actor.
- Admin: the group index is organised by category, a definitions page browses and
  filters every tunable, a per-definition page edits manual tags, and a tag admin
  creates and edits tags by hand. Seeded tags cannot be deleted or renamed there.
- `tunables.access.check_group_editable(request, group)` for callers that hold a group
  name and no change list.

## 0.2.0, 2026-09-18

Reads survive a catalogue rollout, snapshots can be republished and diffed, imports can
replace the whole state, and the type system gains mappings and catalogue-level rules.

- Endpoints that serve stored data, the values, change sets, snapshots, export and the
  new diff, answer while the catalogue in code differs from the database. Catalogue
  reads and all writes still answer `503` until `tunables_sync` runs. New `GET status/`
  reporting the sync state, versions, catalogue hashes, validator descriptions,
  publisher states and rule violations.
- `tunables_export --defaults` writes the version-zero document from the catalogue
  alone, and `--schema` writes a JSON Schema specific to the catalogue in which every
  group and tunable is a required, typed property. `GET snapshots/schema/` serves the
  same schema.
- New `Mapping(key, value, min_entries, max_entries)` type for keyed tables, with string
  keys checked by a key type and values by a value type; edited as JSON in the admin.
- `Catalogue(validators=[...])` for rules that span groups, run after the group
  validators on every write and dry run, reported with scope `catalogue`.
- Publisher outcomes are recorded per publisher and shown in `status/`;
  `tunables_publish [VERSION] [--publisher PATH]` resends a snapshot.
- `GET diff/?from=&to=` returns per-key changes between two versions.
- `tunables_import --replace` and `POST import/?mode=replace` reset every override the
  document does not name, so the document becomes the complete state.
- `EDITABLE_GROUPS` applies in the admin as well as the API.
- Messages and admin text go through Django translation; error codes stay the contract.
- New setting `REASON_HEADER`, default `X-Tunables-Reason`, for the `PATCH` reason header.

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
