# Releasing

A release reaches PyPI through GitHub Actions. Publishing a GitHub release runs the `CI`
workflow in `.github/workflows/release.yml`. Its `publish` job waits for lint, the test
matrix, the Postgres tests and both wheel checks, then runs `uv build` and `uv publish`
with the `PYPI_API_TOKEN` repository secret.

## During the cycle

A pull request that changes something a host would notice adds its entry to a
`## Unreleased` section at the top of `CHANGELOG.md`. Start that section if it is not there.
Entries go under "Changes to existing behaviour:" or "Additions:", as in the earlier
sections, and the section opens with a sentence or two on what the release is about.

## Prepare the release

The release gets its own branch and pull request, with one commit and no code changes.

1. Branch `release-X.Y.Z` from an up-to-date master.
2. Rename `## Unreleased` to `## X.Y.Z, YYYY-MM-DD`, dated the day you tag.
3. Set `version` in `pyproject.toml`, then run `uv lock` so `uv.lock` records it.
4. Run the checks from the README's Development section. `pytest --postgres` needs Docker,
   and the two check scripts build the wheel, so they test what will be uploaded.
5. Commit as `Release X.Y.Z`, open the pull request, and merge it once CI is green.

If the merge slips to a later day, update the date in the heading first.

## Tag and publish

From master after the merge:

```
git switch master && git pull --ff-only
git tag -a vX.Y.Z -m "Release X.Y.Z"
git push origin vX.Y.Z
awk '/^## X\.Y\.Z,/{f=1; next} /^## /{f=0} f' CHANGELOG.md > notes.md
gh release create vX.Y.Z --title X.Y.Z --notes-file notes.md --verify-tag
```

The notes are the body of the changelog section without its heading. Delete `notes.md`
afterwards; it is not part of the repository.

Follow the run with `gh run watch`. When the `publish` job passes, the new version shows
on https://pypi.org/project/django-tunables/ with a wheel and an sdist.
