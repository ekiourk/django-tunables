# Deploying a new catalogue

The catalogue lives in code, and the database holds a mirror of it plus a hash of its
structure. `tunables_sync` brings the two together. Between a deploy and that command
the code and the database disagree, and some of the package stops answering.

## What the hash covers

The hash covers structure: which groups and tunables exist, their types, their defaults.
Titles, descriptions, categories, sections and seeded tags stay out of it. Retitling a
tunable or moving a group into another category therefore leaves the mirror stale
without putting the deployment out of sync, and the next `tunables_sync` updates the
mirror quietly. Adding, removing, renaming or retyping a tunable, or changing a default,
changes the hash and opens the window.

## Inside the window

| Surface | Behaviour |
|---|---|
| `values/`, `changesets/`, `snapshots/`, `export/`, `diff/`, the tag reads | Serve as usual from stored data |
| `status/` | `200`, with `synced: false` and both hashes |
| `groups/`, `definitions/`, the schema endpoints | `503 catalogue-out-of-sync` |
| Every write, through the API or the admin | `503`, and the admin hides its edit links |
| `values.get()` in your own code | Serves overrides for the keys the running code declares |

Readers of snapshot documents and of the two reader tables keep working throughout,
since the last committed snapshot stays the current one until a new version is written.

## Rolling deployments

During a rollout two versions of the code run at once, and only one of them matches the
hash in the database. Before `tunables_sync` the new pods are out of sync; after it the
old ones are. Either way, one set answers `503` on the catalogue reads and refuses
writes while both are up.

The window closes fastest when the sync runs as its own step between `migrate` and the
rollout of application traffic, the same place a data migration would go:

```
python manage.py migrate
python manage.py tunables_sync
```

A few things to arrange around it:

- Run `tunables_sync --check` in a release pipeline to find out whether a deploy will
  open the window at all. It exits 1 when the stored hash differs from the code, or when
  the mirrored categories or seeded tags differ, and writes nothing.
- Keep the rollout short for catalogue changes that move the hash, and prefer a
  maintenance moment for large ones.
- A deploy that only rewords titles or regroups tunables never opens the window, so it
  needs no coordination beyond running the sync afterwards.
- Point health probes at `status/`, which answers `200` throughout. A catalogue
  endpoint makes a probe fail during the window.
