from typing import Any

from django.db import models
from django.utils import timezone

from tunables.errors import HistoryIsAppendOnly


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

    def __str__(self) -> str:
        return f"version {self.current_version}"


class TunableDefinition(models.Model):
    key = models.CharField(max_length=255, unique=True)
    group_name = models.CharField(max_length=100)
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

    class Meta:
        ordering = ["group_name", "order", "name"]

    def __str__(self) -> str:
        return self.key


class AppendOnlyQuerySet(models.QuerySet["AppendOnlyModel"]):
    def update(self, **_: Any) -> int:
        raise HistoryIsAppendOnly("history rows cannot be updated")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise HistoryIsAppendOnly("history rows cannot be deleted")


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

    def __str__(self) -> str:
        return f"change set {self.version}"


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

    def __str__(self) -> str:
        return self.key


class TunableValue(models.Model):
    key = models.CharField(max_length=255, unique=True)
    definition = models.ForeignKey(TunableDefinition, on_delete=models.PROTECT)
    value = models.JSONField()
    changeset = models.ForeignKey(ChangeSet, on_delete=models.PROTECT)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.key


class Snapshot(AppendOnlyModel):
    version = models.PositiveIntegerField(unique=True)
    changeset = models.OneToOneField(ChangeSet, on_delete=models.PROTECT, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    format_version = models.PositiveSmallIntegerField()
    catalogue_version = models.CharField(max_length=80)
    document = models.JSONField()

    class Meta:
        ordering = ["-version"]

    def __str__(self) -> str:
        return f"snapshot {self.version}"
