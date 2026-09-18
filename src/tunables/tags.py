from collections.abc import Sequence

from django.db import transaction

from tunables.errors import TagExists, TagSeeded
from tunables.identifiers import TAG
from tunables.models import Tag, TunableDefinition, TunableDefinitionTag


def _check_name(name: str) -> None:
    if not TAG.match(name):
        raise ValueError(f"tag name {name!r} must match {TAG.pattern}")


def create_tag(name: str, description: str = "") -> Tag:
    """Create a manual tag. Raises TagExists, or ValueError for a bad name."""
    _check_name(name)
    if Tag.objects.filter(name=name).exists():
        raise TagExists(name)
    return Tag.objects.create(name=name, description=description)


def update_tag(name: str, description: str) -> Tag:
    """Change a tag's description. Raises Tag.DoesNotExist."""
    tag = Tag.objects.get(name=name)
    tag.description = description
    tag.save(update_fields=["description"])
    return tag


def delete_tag(name: str) -> None:
    """Delete a manual tag and its assignments. Raises Tag.DoesNotExist, or TagSeeded for a seeded tag."""
    tag = Tag.objects.get(name=name)
    if tag.from_catalogue:
        raise TagSeeded(name)
    tag.delete()


@transaction.atomic
def set_manual_tags(key: str, names: Sequence[str]) -> list[str]:
    """Replace the manual tags of one definition, creating unknown names. Returns every tag on it, sorted."""
    for name in names:
        _check_name(name)
    definition = TunableDefinition.objects.get(key=key)
    wanted = set(names)
    rows = TunableDefinitionTag.objects.filter(definition=definition).select_related("tag")
    seeded = {row.tag.name for row in rows if row.seeded}
    rows.filter(seeded=False).exclude(tag__name__in=wanted).delete()
    for name in wanted - seeded:
        tag, _ = Tag.objects.get_or_create(name=name)
        TunableDefinitionTag.objects.get_or_create(definition=definition, tag=tag, defaults={"seeded": False})
    return sorted(TunableDefinitionTag.objects.filter(definition=definition).values_list("tag__name", flat=True))
