import logging
from collections.abc import Sequence
from functools import partial

from django.db import transaction
from django.utils import timezone

from tunables.errors import TagExists, TagNotAllowed, TagSeeded
from tunables.identifiers import TAG
from tunables.models import Tag, TunableDefinition, TunableDefinitionTag

logger = logging.getLogger(__name__)

SYSTEM = "system"


def _who(actor: str) -> str:
    return actor or "an unnamed caller"


def _log(message: str, *args: object) -> None:
    """Log after the transaction commits, so a rolled back change leaves no line."""
    transaction.on_commit(partial(logger.info, message, *args))


def _check_name(name: str) -> None:
    if not TAG.match(name):
        raise ValueError(f"tag name {name!r} must match {TAG.pattern}")


def create_tag(name: str, description: str = "", *, actor: str = "") -> Tag:
    """Create a manual tag. Raises TagExists, or ValueError for a bad name."""
    _check_name(name)
    if Tag.objects.filter(name=name).exists():
        raise TagExists(name)
    tag = Tag.objects.create(name=name, description=description)
    _log("tag %r created by %s", name, _who(actor))
    return tag


def update_tag(name: str, description: str, *, actor: str = "") -> Tag:
    """Change a tag's description. Raises Tag.DoesNotExist."""
    tag = Tag.objects.get(name=name)
    tag.description = description
    tag.save(update_fields=["description"])
    _log("tag %r described by %s", name, _who(actor))
    return tag


def log_seed_removed(key: str, name: str, *, kept: bool) -> None:
    """Log tunables_sync dropping a seed, whether the row survives as a manual assignment or goes."""
    outcome = "kept as a manual assignment" if kept else "removed"
    _log("seed tag %r of %r dropped by the catalogue, %s", name, key, outcome)


def log_admin_save(before: str | None, after: str, *, actor: str = "") -> None:
    """Log a save from the tag admin, which edits the row rather than calling the helpers."""
    if before is None:
        _log("tag %r created by %s", after, _who(actor))
    elif before != after:
        _log("tag %r renamed to %r by %s", before, after, _who(actor))
    else:
        _log("tag %r described by %s", after, _who(actor))


def log_admin_delete(name: str, *, actor: str = "") -> None:
    """Log a delete from the tag admin."""
    _log("tag %r deleted by %s", name, _who(actor))


def delete_tag(name: str, *, actor: str = "") -> None:
    """Delete a manual tag and its assignments. Raises Tag.DoesNotExist, or TagSeeded for a seeded tag."""
    tag = Tag.objects.get(name=name)
    if tag.from_catalogue:
        raise TagSeeded(name)
    tag.delete()
    _log("tag %r deleted by %s", name, _who(actor))


@transaction.atomic
def set_manual_tags(key: str, names: Sequence[str], *, actor: str = "", may_create: bool = True) -> list[str]:
    """Replace the manual tags of one definition, creating unknown names. Returns every tag on it, sorted.

    With may_create false, a name that no tag carries raises TagNotAllowed instead of creating it.
    """
    for name in names:
        _check_name(name)
    if not may_create:
        known = set(Tag.objects.filter(name__in=names).values_list("name", flat=True))
        for name in names:
            if name not in known:
                raise TagNotAllowed(name)
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
        _log("tags of %r set by %s: was [%s], now [%s]", key, _who(actor), ", ".join(before), ", ".join(after))
    return after
