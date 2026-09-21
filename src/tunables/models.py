from typing import Any, TypeVar

from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from tunables.errors import HistoryIsAppendOnly
from tunables.identifiers import TAG


class ActorSource(models.TextChoices):
    VERIFIED = "verified"
    ASSERTED = "asserted"
    SYSTEM = "system"


class ChangeSource(models.TextChoices):
    ADMIN = "admin"
    API = "api"
    IMPORT = "import"
    ROLLBACK = "rollback"
    SYSTEM = "system"


class State(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, editable=False)
    current_version = models.PositiveIntegerField(default=0)
    catalogue_version = models.CharField(max_length=80, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name="tunables_state_single_row")]

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.pk = 1
        super().save(*args, **kwargs)


class Tag(models.Model):
    """A free-form label on definitions, seeded from the catalogue or created by hand."""

    name = models.CharField(max_length=64, unique=True, validators=[RegexValidator(TAG.pattern)])
    description = models.TextField(blank=True)
    from_catalogue = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return self.name


class TunableDefinition(models.Model):
    key = models.CharField(max_length=255, unique=True)
    group_name = models.CharField(max_length=100)
    category_name = models.CharField(max_length=100, default="general")
    name = models.CharField(max_length=100)
    order = models.IntegerField()
    type_name = models.CharField(max_length=100)
    type_params = models.JSONField()
    default = models.JSONField()
    title = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    unit = models.CharField(max_length=255, blank=True)
    ui = models.JSONField(default=dict)
    metadata = models.JSONField(default=dict)
    deprecated = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    synced_at = models.DateTimeField()
    tags = models.ManyToManyField(Tag, through="TunableDefinitionTag", related_name="definitions", blank=True)

    class Meta:
        ordering = ["group_name", "order", "name"]
        verbose_name = _("tunable")
        verbose_name_plural = _("tunables")


class TunableDefinitionTag(models.Model):
    """One tag on one definition. Seeded rows come from the catalogue and are managed by tunables_sync."""

    definition = models.ForeignKey(TunableDefinition, on_delete=models.CASCADE)
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE)
    seeded = models.BooleanField(default=False)
    assigned_at = models.DateTimeField(default=timezone.now)
    assigned_by = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["definition", "tag"], name="tunables_definitiontag_unique"),
        ]


_M = TypeVar("_M", bound="AppendOnlyModel")


class AppendOnlyQuerySet(models.QuerySet[_M]):
    def update(self, **_: Any) -> int:
        raise HistoryIsAppendOnly("history rows cannot be updated")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise HistoryIsAppendOnly("history rows cannot be deleted")

    def delete_rows(self) -> int:
        """Delete the selected rows for real. Snapshot retention is the only sanctioned caller."""
        return int(models.QuerySet.delete(self)[0])


class AppendOnlyModel(models.Model):
    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise HistoryIsAppendOnly("history rows cannot be updated")
        kwargs["force_insert"] = True
        super().save(*args, **kwargs)

    def delete(self, *_: Any, **__: Any) -> tuple[int, dict[str, int]]:
        raise HistoryIsAppendOnly("history rows cannot be deleted")


class ChangeSet(AppendOnlyModel):
    version = models.PositiveIntegerField(unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    actor = models.CharField(max_length=255)
    actor_source = models.CharField(max_length=20, choices=ActorSource.choices)
    client = models.CharField(max_length=255, blank=True)
    reason = models.TextField(blank=True)
    source = models.CharField(max_length=20, choices=ChangeSource.choices)
    restores_version = models.PositiveIntegerField(null=True, blank=True)
    request_id = models.CharField(max_length=255, blank=True)
    catalogue_version = models.CharField(max_length=80)
    metadata = models.JSONField(default=dict)

    class Meta:
        ordering = ["-version"]


class ChangeItem(AppendOnlyModel):
    changeset = models.ForeignKey(ChangeSet, on_delete=models.CASCADE, related_name="items")
    key = models.CharField(max_length=255)
    definition = models.ForeignKey(TunableDefinition, on_delete=models.PROTECT, null=True, blank=True)
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    reset = models.BooleanField(default=False)

    class Meta:
        ordering = ["key"]
        constraints = [models.UniqueConstraint(fields=["changeset", "key"], name="tunables_changeitem_unique_key")]


class TunableValue(models.Model):
    key = models.CharField(max_length=255, unique=True)
    definition = models.ForeignKey(TunableDefinition, on_delete=models.PROTECT)
    value = models.JSONField()
    changeset = models.ForeignKey(ChangeSet, on_delete=models.PROTECT)
    updated_at = models.DateTimeField(auto_now=True)


class Snapshot(AppendOnlyModel):
    version = models.PositiveIntegerField(unique=True)
    changeset = models.OneToOneField(ChangeSet, on_delete=models.PROTECT, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    format_version = models.PositiveSmallIntegerField()
    catalogue_version = models.CharField(max_length=80)
    document = models.JSONField()

    class Meta:
        ordering = ["-version"]


class PublisherState(models.Model):
    """Bookkeeping per configured publisher: the version it last received and the last failure."""

    publisher = models.CharField(max_length=255, unique=True)
    last_version = models.PositiveIntegerField(null=True, blank=True)
    last_published_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)
