from collections.abc import Collection, Sequence

from tunables.catalogue import Catalogue, Group, Tunable
from tunables.errors import UnknownTag
from tunables.models import Tag, TunableDefinitionTag


def require_tags(names: Collection[str]) -> None:
    """Raise UnknownTag for the first name, in request order, that no Tag row carries."""
    if not names:
        return
    known = set(Tag.objects.filter(name__in=names).values_list("name", flat=True))
    for name in names:
        if name not in known:
            raise UnknownTag(name)


def require_keys(catalogue: Catalogue, keys: Collection[str]) -> None:
    """Raise UnknownKey for the first key, in request order, that the catalogue does not declare."""
    for key in keys:
        catalogue.get(key)


def tags_by_key() -> dict[str, list[str]]:
    """Every tag name per definition key, seeded and manual, from the mirror."""
    by_key: dict[str, list[str]] = {}
    for key, name in TunableDefinitionTag.objects.values_list("definition__key", "tag__name"):
        by_key.setdefault(key, []).append(name)
    return by_key


def match_definitions(
    catalogue: Catalogue,
    tags: dict[str, list[str]],
    *,
    category: str | None = None,
    group: str | None = None,
    wanted_tags: Collection[str] = (),
    wanted_keys: Collection[str] = (),
    query: str = "",
) -> list[tuple[Group, Tunable, list[str]]]:
    """Definitions in catalogue order narrowed by category, group, tags (all must be present), keys and text.

    Category, group and keys must already be known to exist; the caller decides how to report unknown names.
    """
    groups: Sequence[Group] = list(catalogue.groups.values())
    if category is not None:
        groups = catalogue.groups_in(category)
    if group is not None:
        groups = [g for g in groups if g.name == group]
    wanted = set(wanted_tags)
    keys = set(wanted_keys)
    needle = query.casefold()
    matches = []
    for g in groups:
        for tunable in g.tunables:
            key = f"{g.name}.{tunable.name}"
            if keys and key not in keys:
                continue
            own_tags = tags.get(key, [])
            if wanted and not wanted <= set(own_tags):
                continue
            haystack = " ".join((key, str(tunable.title), str(tunable.description))).casefold()
            if needle and needle not in haystack:
                continue
            matches.append((g, tunable, own_tags))
    return matches
