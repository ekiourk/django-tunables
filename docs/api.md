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

## Headers

| Header | Direction | Meaning |
|---|---|---|
| `X-Tunables-Version` | response | The current version, on every response including errors. After a write it is the new version. |
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
`403 forbidden-group` problem.

## Read endpoints

Endpoints that describe the catalogue in code, `groups/`, `groups/{group}/`, the two
schema endpoints and `definitions/`, first check that the code matches the database.
If not, the answer is `503 catalogue-out-of-sync` until `tunables_sync` has run.
Endpoints that serve stored data, the values, change sets, snapshots and the export,
answer from the latest snapshot regardless, so a deployment whose sync has not run yet
keeps serving the previous state. Every write requires sync.

| Method and path | Response |
|---|---|
| `GET groups/` | `[group summary]` in catalogue order |
| `GET groups/{group}/` | group summary without `tunable_count`, plus `ui`, `metadata` and `definitions: [definition]` |
| `GET groups/{group}/schema/` | `{"json_schema": ..., "ui_schema": ...}` |
| `GET schema/` | `{"<group>": {"json_schema": ..., "ui_schema": ...}}` for every group |
| `GET definitions/` | `[definition]` for every tunable in catalogue order |
| `GET values/` | `{"version", "groups": {group: {name: value}}, "overridden": [key]}` with `ETag` |
| `GET groups/{group}/values/` | `{"version", "values": {name: value}, "overridden": [name]}` with `ETag` |
| `GET changesets/` | paginated `[change set]`, newest first |
| `GET changesets/{version}/` | change set with `items` |
| `GET snapshots/latest/` | the snapshot document, see `snapshot-format.md`, with `ETag` |
| `GET snapshots/{version}/` | the same for one version |
| `GET export/` | the latest document as a download, `Content-Disposition: attachment; filename="tunables-v42.json"` |
| `GET status/` | `{"synced", "version", "catalogue_version", "code_catalogue_version"}`, always `200`; `version` and `catalogue_version` are `null` before the first sync |

Shapes:

```
group summary = {name, title, description, order, tunable_count, validators: [text]}
definition    = {key, group, name, type: {name, params}, default, title, description, unit, ui, metadata, deprecated}
change set    = {version, created_at, actor, actor_source, client, reason, source, restores_version,
                 request_id, catalogue_version, metadata, item_count}
item          = {key, old_value, new_value, reset}
```

`created_at` is ISO 8601 in UTC with a `Z` suffix. `old_value` is `null` when the
key was at its default before the change. `new_value` is `null` for a reset.
`actor_source` is one of `verified`, `asserted`, `system`. `source` is one of `admin`,
`api`, `import`, `rollback`, `system`.

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
| `POST changesets/` | `{"changes": [...], "reason": "", "dry_run": false}` | applies the changes as one change set |
| `POST validate/` | same as above | always a dry run |
| `PATCH groups/{group}/values/` | `{"<name>": value, "<name>": null}` | form-shaped write of one group; `null` resets |
| `POST rollback/` | `{"to_version": 40, "reason": ""}` | restores the overrides of that snapshot |
| `POST import/` | a snapshot document | applies its `groups` as changes; `?strict=1` rejects unknown keys |

Each element of `changes` is either `{"key": "pricing.vat_rate", "value": 0.2}` or
`{"key": "pricing.vat_rate", "reset": true}`. A `value` of `null` is a `type` validation
error. To return a key to its default, send `reset`.

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
`unknown_key`, unless `?strict=1` turns them into errors. Only keys present in the
document are touched; a document is not a full desired state. Importing a document
that matches the current values is `400 nothing-to-change`.

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
    {"group": "weights", "code": "group", "detail": "weights must sum to 1"}
  ]
}
```

| `type` | Status | Extra members | When |
|---|---|---|---|
| `urn:tunables:problem:validation-failed` | 422 | `errors` | at least one change is invalid; every error is listed |
| `urn:tunables:problem:version-conflict` | 412 | `expected_version`, `current_version` | `If-Match` did not match |
| `urn:tunables:problem:nothing-to-change` | 400 | | no change would alter a value |
| `urn:tunables:problem:unknown-version` | 422 | `version` | `rollback` to a version with no snapshot |
| `urn:tunables:problem:forbidden-group` | 403 | `group` | `EDITABLE_GROUPS` excludes a touched group |
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
| `unknown_key` | the key is not in the catalogue |
| `duplicate` | the key appears twice in one request |
| `group` | a group validator, reported with `group` instead of `key` |

Custom types define their own codes. Inside a `List`, an item error keeps the item's
code and its `detail` starts with the index, such as `[1]: must be >= 0`.

### Warning codes

| Code | Meaning |
|---|---|
| `deprecated` | a changed key is deprecated; `detail` carries the reason |
| `unknown_key` | on import without `strict`, a key was skipped |

## OpenAPI

The package does not ship an OpenAPI document or annotations, and this page is the
reference. The views are plain `APIView` subclasses, so a host that already uses
drf-spectacular gets a basic schema for them by mounting its schema view as usual.
