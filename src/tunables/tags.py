import logging
from collections.abc import Sequence

from django.db import transaction
from django.utils import timezone

from tunables.errors import TagExists, TagSeeded
from tunables.identifiers import TAG
from tunables.models import Tag, TunableDefinition, TunableDefinitionTag

logger = logging.getLogger(__name__)

SYSTEM = "system"


def _who(actor: str) -> str:
    return actor or "an unnamed caller"


def _check_name(name: str) -> None:
    if not TAG.match(name):
        raise ValueError(f"tag name {name!r} must match {TAG.pattern}")


def create_tag(name: str, description: str = "", *, actor: str = "") -> Tag:
    """Create a manual tag. Raises TagExists, or ValueError for a bad name."""
    _check_name(name)
    if Tag.objects.filter(name=name).exists():
        raise TagExists(name)
    tag = Tag.objects.create(name=name, description=description)
    logger.info("tag %r created by %s", name, _who(actor))
    return tag


def update_tag(name: str, description: str, *, actor: str = "") -> Tag:
    """Change a tag's description. Raises Tag.DoesNotExist."""
    tag = Tag.objects.get(name=name)
    tag.description = description
    tag.save(update_fields=["description"])
    logger.info("tag %r described by %s", name, _who(actor))
    return tag


def delete_tag(name: str, *, actor: str = "") -> None:
    """Delete a manual tag and its assignments. Raises Tag.DoesNotExist, or TagSeeded for a seeded tag."""
    tag = Tag.objects.get(name=name)
    if tag.from_catalogue:
        raise TagSeeded(name)
    tag.delete()
    logger.info("tag %r deleted by %s", name, _who(actor))


@transaction.atomic
def set_manual_tags(key: str, names: Sequence[str], *, actor: str = "") -> list[str]:
    """Replace the manual tags of one definition, creating unknown names. Returns every tag on it, sorted."""
    for name in names:
        _check_name(name)
    definition = TunableDefinition.objects.get(key=key)
    wanted = set(names)
    rows = TunableDefinitionTag.objects.filter(definition=definition).select_related("tag")
    before = sorted(row.tag.name for row in rows)
    seeded = {row.tag.name for row in rows if row.seeded}
    rows.filter(seeded=False).exclude(tag__name__in=wanted).delete()
    for name in wanted - seeded:
        tag, _ = Tag.objects.get_or_create(name=name)
        TunableDefinitionTag.objects.get_or_create(
            definition=definition,
            tag=tag,
            defaults={"seeded": False, "assigned_by": actor, "assigned_at": timezone.now()},
        )
    after = sorted(TunableDefinitionTag.objects.filter(definition=definition).values_list("tag__name", flat=True))
    if after != before:
        logger.info("tags of %r set by %s: was [%s], now [%s]", key, _who(actor), ", ".join(before), ", ".join(after))
    return after
