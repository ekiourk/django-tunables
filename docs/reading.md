# Reading values in Django code

The host application reads its own tunables through one object:

```python
from tunables import values

rate = values.get("pricing.vat_rate")  # 0.24, a float
pricing = values.group("pricing")  # {"vat_rate": 0.24, ...}
everything = values.all()  # {"pricing": {...}, "thermostat": {...}}
```

Values come back as Python objects of the tunable's type, defaults overlaid with the
stored overrides. A key the catalogue does not declare raises `UnknownKey`, and an
unknown group name raises `CatalogueError`. Both mean the calling code names something
that does not exist.

## Caching

Each process keeps the whole value set in memory. A read serves from that memory. At
most once per `TUNABLES["READ_CACHE_TTL"]` seconds a read also asks the database for the
current version, one indexed lookup, and reloads the values only when the version moved.
The default is one second, so a busy view issues at most one small query per second per
process and none at all in between.

| `READ_CACHE_TTL` | Behaviour |
|---|---|
| `1.0`, the default | At most one version check per second per process. |
| `0` | A version check on every read. Values still reload only when the version moved. |
| `None` | No version check. The cache lives until a write in this process or a call to `values.invalidate()`. |

Every write in the process that made it drops the cache immediately, so a view that
changes a value and reads it back gets the new one. Writes made elsewhere, by another
worker, the admin of another pod, or a management command, reach a process through the
version check, which puts the worst-case staleness at the length of the window.

`values.version` is the version the cached values came from, and `values.invalidate()`
drops the cache by hand, which tests and long-running commands sometimes want.

## Before the first sync

The reader never refuses to answer. A database with no state row serves the defaults
declared in code at version 0, so a process that starts before `tunables_sync` still
runs. A catalogue whose structure no longer matches the mirror keeps serving overrides
for the keys the running code declares, since the API's `503` exists to protect writes,
not reads in your own code.

## Choosing between the reader and a snapshot

Use the reader for code inside the Django project: views, tasks, management commands,
anything that imports the catalogue. Use a snapshot document for separate programs that
do not import the package. The [snapshot format](snapshot-format.md) describes that
path, with the polling contract and the tables involved.
