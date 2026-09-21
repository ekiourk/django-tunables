# REST API

The API is a Django REST Framework app. Mount it wherever you like:

```python
urlpatterns = [
    path("api/tunables/", include("tunables.api.urls")),
]
```

Paths below are relative to that mount point. All request and response bodies are JSON.

## Authentication and permissions

The package ships no authentication class. By default the views use the host's DRF
settings, `DEFAULT_AUTHENTICATION_CLASSES` and `DEFAULT_PERMISSION_CLASSES`. To give
the tunables API its own rules without touching the rest of the project, set either
or both of these to lists of dotted paths or classes:

```python
TUNABLES = {
    "CATALOGUE": "myproject.tunables_catalogue.catalogue",
    "API_AUTHENTICATION_CLASSES": ["rest_framework.authentication.TokenAuthentication"],
    "API_PERMISSION_CLASSES": ["rest_framework.permissions.IsAdminUser"],
}
```

Read and write endpoints share these settings. To let some clients read and only some
write, use a DRF permission class that inspects `request.method`, or restrict writes
per group with `EDITABLE_GROUPS` below.

### Using the admin's permissions

The admin asks Django's permission system and the API asks DRF, so the two answer
separately. `tunables.api.permissions.TunablesPermissions` points them at the same
answers for a host that wants one model:

```python
TUNABLES = {
    "CATALOGUE": "myproject.tunables_catalogue.catalogue",
    "API_PERMISSION_CLASSES": ["tunables.api.permissions.TunablesPermissions"],
}
```

| Request | Permission |
|---|---|
| any read | `tunables.view_tunabledefinition` |
| `POST tags/` | `tunables.add_tag` |
| `PATCH tags/{name}/`, `PUT definitions/{key}/tags/` | `tunables.change_tag` |
| `DELETE tags/{name}/` | `tunables.delete_tag` |
| every other write | `tunables.add_changeset` |

The class needs a request with a user, so it suits session or token authentication
backed by Django accounts. A deployment that authenticates without user accounts keeps
its own permission class, which is the default. `EDITABLE_GROUPS` still applies on top,
narrowing writes by group.

## Headers

| Header | Direction | Meaning |
|---|---|---|
| `X-Tunables-Version` | response | The current version, read after the handler, so it is never older than the body. On every response including errors. After a write it is the new version. |
| `ETag` | response | `"<version>"` on `values/`, `groups/{group}/values/`, `snapshots/latest/` and `snapshots/{version}/`. |
| `If-None-Match` | request | On the endpoints above, a matching tag answers `304 Not Modified` with no body. |
| `If-Match` | request | On writes, `"<version>"` sets the expected version. A mismatch is `412`. `*` or no header means the write is applied regardless. |
| `X-Tunables-Actor` | request | Who is acting, when the request is not authenticated. Recorded with `actor_source: asserted`. Name configurable with `ACTOR_HEADER`. |
| `X-Tunables-Client` | request | Which program is acting. Recorded as `client`. Name configurable with `CLIENT_HEADER`. |
| `X-Request-ID` | request | Recorded as `request_id` on the change set. Name configurable with `REQUEST_ID_HEADER`. |
| `X-Tunables-Reason` | request | Reason for a `PATCH` of group values, whose body has no room for one. Name configurable with `REASON_HEADER`. |

## Actors

Every change set records who made it and how that identity was established.
`TUNABLES["ACTOR_RESOLVER"]` names a callable taking the DRF request and returning a
`tunables.Actor`. The default resolver works like this:

1. An authenticated `request.user` gives `Actor(user.get_username(), "verified")`.
2. Otherwise a present `X-Tunables-Actor` header gives `Actor(header, "asserted")`.
3. Otherwise `Actor("anonymous", "asserted")`.

In all three cases `client` comes from `X-Tunables-Client`. Replace the resolver to
map, for example, an API token to a service name.

## Restricting writes per group

`TUNABLES["EDITABLE_GROUPS"]` names a callable taking the request and returning the
collection of group names the request may change, or `None` for all groups. It is
consulted on every write, including dry runs, on the exact changes the write would
make. A rollback that would only restore `pricing` values is allowed for a caller who
may edit `pricing`. The first group outside the collection is reported as a
`403 forbidden-group` problem. The admin applies the same callable to its edit form and
rollback action, passing its Django `HttpRequest`; the API passes DRF's `Request`, which
proxies attribute access to the underlying `HttpRequest`, so a callable that reads
`request.user` or `request.headers` works for both.

## Read endpoints

The index at the mount point lists `categories`, `groups`, `definitions`, `values`,
`tags`, `changesets`, `snapshots`, `schema` and `status`. Each URL is absolute and built
from the request, so it stays correct behind an ingress or a path prefix. Routes that take a
group name or a version are reached from their collection, and `snapshots` points at
`snapshots/latest/`. The route is named `index`, so a host that prefers no root view can
write its own URLconf and leave it out.

Endpoints that describe the catalogue in code, `groups/`, `groups/{group}/`, the two
schema endpoints and `definitions/`, first check that the code matches the database.
If not, the answer is `503 catalogue-out-of-sync` until `tunables_sync` has run.
Endpoints that serve stored data, the values, change sets, snapshots, the export, the
diff and the two tag reads, answer from the database regardless, so a deployment whose
sync has not run yet keeps serving the previous state. The index answers regardless too,
since it describes routes rather than the catalogue, so the base URL still works for
someone diagnosing the sync state. Every write requires sync.

| Method and path | Response |
|---|---|
| `GET` at the mount point | `{"<name>": "<absolute url>"}` for the nine routes below, as a starting point for a browser or a client |
| `GET categories/` | `[{name, title, description, order, groups: [names]}]` in catalogue order; `general` is always present |
| `GET groups/[?category=]` | `[group summary]` in catalogue order, optionally one category's groups; unknown category is `404` |
| `GET groups/{group}/` | group summary without `tunable_count`, plus `ui`, `metadata` and `definitions: [definition]` |
| `GET groups/{group}/schema/[?tag=&tag=]` | `{"json_schema": ..., "ui_schema": ...}`; with `tag` only the tunables carrying every named tag are described, and `404` when none does |
| `GET schema/[?tag=&tag=]` | `{"<group>": {"json_schema": ..., "ui_schema": ...}}` for every group; with `tag` each group is filtered the same way and groups left with no tunable are omitted |
| `GET definitions/[?category=&group=&tag=&tag=&q=]` | `[definition]` in catalogue order; filters combine; `tag` repeats and every named tag must be present; `q` is a case-insensitive substring of the key, title or description; unknown category or group is `404` |
| `GET tags/` | `[{name, description, from_catalogue, definition_count}]` by name |
| `GET tags/{name}/` | the same plus `definitions: [keys]` in catalogue order; unknown is `404` |
| `GET values/` | `{"version", "groups": {group: {name: value}}, "overridden": [key]}` with `ETag` |
| `GET groups/{group}/values/` | `{"version", "values": {name: value}, "overridden": [name]}` with `ETag` |
| `GET changesets/` | paginated `[change set]`, newest first |
| `GET changesets/{version}/` | change set with `items` |
| `GET snapshots/latest/` | the snapshot document, see `snapshot-format.md`, with `ETag` |
| `GET snapshots/{version}/` | the same for one version |
| `GET snapshots/schema/` | JSON Schema of snapshot documents for this catalogue, see `snapshot-format.md`; requires sync |
| `GET export/` | the latest document as a download, `Content-Disposition: attachment; filename="tunables-v42.json"` |
| `GET diff/?from=40&to=47` | `{"from", "to", "changes": [{key, old, new}]}` over the two stored documents, keys sorted, equal values omitted; a key present in only one document has `null` on the other side; both parameters required, unknown version is `404` |
| `GET status/` | `{"synced", "version", "catalogue_version", "code_catalogue_version", "validators"}`, always `200`; `version` and `catalogue_version` are `null` before the first sync; `validators` lists the catalogue-level rules; `publishers` lists each configured publisher with `last_version`, `last_published_at` and `last_error`; `rule_violations` lists the group and catalogue rules the stored values break, in the same shape as the `errors` of a validation problem |

Shapes:

```
group summary = {name, title, description, order, category, tunable_count, validators: [text]}
definition    = {key, group, name, type: {name, params}, default, title, description, unit, ui, metadata, deprecated,
                 category, tags: [names]}
change set    = {version, created_at, actor, actor_source, client, reason, source, restores_version,
                 request_id, catalogue_version, metadata, item_count}   # metadata comes from the POST body, {} otherwise
item          = {key, old_value, new_value, reset}
```

`created_at` is ISO 8601 in UTC with a `Z` suffix. `old_value` is `null` when the
key was at its default before the change. `new_value` is `null` for a reset.
`actor_source` is one of `verified`, `asserted`, `system`. `source` is one of `admin`,
`api`, `import`, `rollback`, `system`.

### Schemas by tag

Every property of a group schema carries `x-tags`, the sorted tag names of that tunable
as the database holds them, seeded and manual alike, or `[]` when it has none. The same
lists appear in the group schemas embedded in `GET snapshots/schema/`. The file written
by `tunables_export --schema` has `[]` everywhere, since the command reads no database.

The `tag` parameter of the two schema endpoints repeats and combines with AND, as on
`definitions/`. A filtered group schema keeps its `$id`, its `x-validators` in full and
`additionalProperties: false`; only `properties` shrinks to the matching tunables, and
the UI schema keeps only their controls. A section left with no controls disappears from
the layout. A client that renders a filtered form should expect the server to enforce
group rules over the whole group, including tunables the form does not show.

All three endpoints check the tag names against the stored tags first. A name that no
tag carries is `422 unknown-tag`, so a typo does not pass for an empty result. A tag
that exists but is assigned to nothing in the requested group is a legitimate empty
result: `404` on `groups/{group}/schema/`, the group omitted from `schema/`, and an
empty list from `definitions/`. Because matching reads the mirror, a tag assigned in
the admin or through `PUT definitions/{key}/tags/` changes the filtered schema at once,
with no `tunables_sync` in between.

### Listing change sets

`GET changesets/` returns `{"count", "next", "previous", "results"}` with page-number
pagination. `?page=N` selects a page; the page size is `TUNABLES["PAGE_SIZE"]`,
default 50. Filters combine:

| Parameter | Matches change sets that |
|---|---|
| `group=<name>` | contain an item in that group |
| `key=<group.name>` | contain an item for exactly that key |
| `actor=<identity>` | were made by exactly that actor |
| `since=<ISO 8601 datetime>` | were created at or after that instant. URL-encode the `+` of a timezone offset. |

## Write endpoints

All writes go through the same validation as the admin. Nothing is written unless
every change in the request is valid. A change whose value equals the current
effective value is dropped silently, and a request that changes nothing is
`400 nothing-to-change`, so every version means something. A value equal to the
tunable's default removes the override if there is one, the same as `reset`, so
`overridden` never lists a key that holds its default.

| Method and path | Body | Effect |
|---|---|---|
| `POST changesets/` | `{"changes": [...], "reason": "", "dry_run": false, "metadata": {}}` | applies the changes as one change set |
| `POST validate/` | same as above | always a dry run |
| `PATCH groups/{group}/values/` | `{"<name>": value, "<name>": null}` | form-shaped write of one group; `null` resets |
| `POST rollback/` | `{"to_version": 40, "reason": ""}` | restores the overrides of that snapshot |
| `POST import/` | a snapshot document | applies its `groups` as changes; `?strict=1` rejects unknown keys; `?mode=replace` also resets every override the document does not name |
| `POST tags/` | `{"name", "description": ""}` | creates a manual tag, `201`; an existing name is `409 tag-exists` |
| `PATCH tags/{name}/` | `{"description"}` | changes the description |
| `DELETE tags/{name}/` | | deletes a manual tag and its assignments, `204`; a seeded tag is `409 tag-seeded` |
| `PUT definitions/{key}/tags/` | `{"tags": ["a", "b"]}` | replaces the manual tags of one definition; seeded tags stay; unknown names are created; answers `{"key", "tags"}` with every tag on the definition |

Each element of `changes` is either `{"key": "pricing.vat_rate", "value": 0.2}` or
`{"key": "pricing.vat_rate", "reset": true}`. A `value` of `null` is a `type` validation
error. To return a key to its default, send `reset`.

`metadata` is an optional JSON object that the change set stores unchanged, for example
a ticket number the caller wants to find again. The package does not read it, and only
`POST changesets/` accepts it.

A successful write answers `201`:

```json
{
  "version": 43,
  "changeset": {"version": 43, "actor": "alice", "items": [...], "...": "..."},
  "warnings": [{"key": "thermostat.legacy_offset", "code": "deprecated", "detail": "deprecated: Use target_c instead."}]
}
```

A dry run answers `200 {"valid": true, "warnings": [...]}` after the same checks,
including `If-Match`, the group restriction and `nothing-to-change`, so a client can
show exactly what the real request would do.

Import skips keys that are not in the catalogue and reports each as a warning with code
`unknown_key`, unless `?strict=1` turns them into errors. By default only keys present in
the document are touched. With `?mode=replace` the document is the complete desired
state: keys it omits return to their defaults, so after the import the stored overrides
are exactly those in the document. The resets this produces are subject to
`EDITABLE_GROUPS` like any other change. Importing a document that matches the current
values is `400 nothing-to-change` in either mode.

## Errors

Errors are RFC 9457 problem documents with content type `application/problem+json`:

```json
{
  "type": "urn:tunables:problem:validation-failed",
  "title": "Validation failed",
  "status": 422,
  "detail": "2 errors",
  "errors": [
    {"key": "pricing.vat_rate", "code": "max", "detail": "must be <= 1.0"},
    {"group": "weights", "code": "group", "detail": "weights must sum to 1"},
    {"scope": "catalogue", "code": "catalogue", "detail": "accepted currencies exceed limits.max_currencies"}
  ]
}
```

An `errors` entry names a `key` for a single tunable, a `group` for a group validator,
or `scope: "catalogue"` for a catalogue validator.

Tag writes change no value and create no change set, so `X-Tunables-Version` is the
same before and after. They require sync like every write. `PUT definitions/{key}/tags/`
is subject to `EDITABLE_GROUPS` through the definition's group; creating, editing and
deleting tags is not, since a tag belongs to no group.

The actor is resolved the same way as for a change set, from `ACTOR_HEADER` or the
authenticated user. It is written to the assignment row as `assigned_by`, alongside
`assigned_at`, and every tag change writes one line at info level to the
`tunables.tags` logger:

```
tag 'review' created by alice
tags of 'pricing.vat_rate' set by alice: was [money], now [money, review]
```

Rows that `tunables_sync` seeds from the catalogue say `system`, and only those rows
do, which is how a later sync knows an assignment was a person's and keeps it when the
seed goes away. A call from a shell leaves `assigned_by` empty and logs "an unnamed
caller". An actor or request id longer than 255 characters is truncated to fit its
column.

A view declares which family it belongs to with `permission_scope`, `"tag"` or the
default `"value"`, so a host subclassing a view inherits the right rule.

`PUT definitions/{key}/tags/` creates a tag it has never seen, which under
`TunablesPermissions` needs `tunables.add_tag`. A caller holding only
`tunables.change_tag` may attach tags that already exist, and naming an unknown one is
`403 forbidden-tag`. Deployments using their own permission class are unaffected.

`code` is the contract; `detail` is text for people. The package routes its messages
through Django's translation machinery, so `detail` comes out in the request's active
language when the host has translations for it, and in English otherwise. Clients that
branch on an error should key on `code`, and clients that show messages can localise by
`code` themselves or display `detail` as it comes.

| `type` | Status | Extra members | When |
|---|---|---|---|
| `urn:tunables:problem:validation-failed` | 422 | `errors` | at least one change is invalid; every error is listed |
| `urn:tunables:problem:version-conflict` | 412 | `expected_version`, `current_version` | `If-Match` did not match |
| `urn:tunables:problem:nothing-to-change` | 400 | | no change would alter a value |
| `urn:tunables:problem:unknown-version` | 422 | `version` | `rollback` to a version with no snapshot |
| `urn:tunables:problem:unknown-tag` | 422 | `tag` | a `tag` query value on `definitions/` or the schema endpoints that names no stored tag; `tag` is the first unknown name |
| `urn:tunables:problem:forbidden-group` | 403 | `group` | `EDITABLE_GROUPS` excludes a touched group |
| `urn:tunables:problem:tag-exists` | 409 | `name` | `POST tags/` with a name that exists |
| `urn:tunables:problem:tag-seeded` | 409 | `name` | `DELETE tags/{name}/` on a tag the catalogue seeds |
| `urn:tunables:problem:forbidden-tag` | 403 | `name` | `PUT definitions/{key}/tags/` names a tag that does not exist and the caller may not create one |
| `urn:tunables:problem:catalogue-out-of-sync` | 503 | | code and database disagree; run `tunables_sync` |
| `urn:tunables:problem:not-found` | 404 | | unknown group, version or page |
| `urn:tunables:problem:invalid` | 400 | `errors` | malformed body, query parameter or `If-Match` header |
| `urn:tunables:problem:not-authenticated` | 401 or 403 | | per DRF: 401 when the first authenticator issues a challenge |
| `urn:tunables:problem:permission-denied` | 403 | | a permission class refused |

Other DRF exceptions map the same way: the status is DRF's and the slug is DRF's error
code with underscores replaced by hyphens, for example `method-not-allowed` or
`parse-error`.

### Field error codes

| Code | Raised by |
|---|---|
| `type` | input of the wrong JSON kind, including `null` |
| `min`, `max` | `Integer`, `Float` bounds |
| `min_length`, `max_length`, `pattern` | `String` |
| `enum` | `Enum` membership |
| `min_items`, `max_items`, `unique` | `List` |
| `min_entries`, `max_entries` | `Mapping` |
| `unknown_key` | the key is not in the catalogue |
| `duplicate` | the key appears twice in one request |
| `group` | a group validator, reported with `group` instead of `key` |
| any | a catalogue validator, reported with `scope: "catalogue"`; the code is whatever the validator raises |

Custom types define their own codes. Inside a `List`, an item error keeps the item's
code and its `detail` starts with the index, such as `[1]: must be >= 0`. Inside a
`Mapping`, it starts with the key, such as `["EUR"]: must be >= 0`.

### Warning codes

| Code | Meaning |
|---|---|
| `deprecated` | a changed key is deprecated; `detail` carries the reason |
| `unknown_key` | on import without `strict`, a key was skipped |

## OpenAPI

The package does not ship an OpenAPI document or annotations, and this page is the
reference. The views are plain `APIView` subclasses, so a host that already uses
drf-spectacular gets a basic schema for them by mounting its schema view as usual.
