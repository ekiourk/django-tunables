from typing import TYPE_CHECKING, Any

from django.contrib import admin
from django.http import HttpRequest, HttpResponse
from django.urls import URLPattern, path

from tunables.models import ChangeItem, ChangeSet, Snapshot, TunableDefinition

if TYPE_CHECKING:
    ModelAdmin = admin.ModelAdmin[Any]
    ItemInline = admin.TabularInline[ChangeItem, ChangeSet]
else:
    ModelAdmin = admin.ModelAdmin
    ItemInline = admin.TabularInline


class ReadOnlyAdmin(ModelAdmin):
    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(TunableDefinition)
class TunableDefinitionAdmin(ReadOnlyAdmin):
    """The app's entry in the admin: a group index and a per-group edit form instead of a changelist."""

    def get_urls(self) -> list[URLPattern]:
        edit = path("edit/<str:group>/", self.admin_site.admin_view(self.edit_group), name="tunables_group_edit")
        return [edit, *super().get_urls()]

    def changelist_view(self, request: HttpRequest, extra_context: dict[str, Any] | None = None) -> HttpResponse:
        raise NotImplementedError

    def edit_group(self, request: HttpRequest, group: str) -> HttpResponse:
        raise NotImplementedError


class ChangeItemInline(ItemInline):
    model = ChangeItem
    fields = ["key", "old_value", "new_value", "reset"]
    readonly_fields = ["key", "old_value", "new_value", "reset"]
    can_delete = False
    extra = 0

    def has_add_permission(self, request: HttpRequest, obj: ChangeSet | None = None) -> bool:
        return False


@admin.register(ChangeSet)
class ChangeSetAdmin(ReadOnlyAdmin):
    inlines = [ChangeItemInline]
    actions = ["rollback"]

    @admin.action(description="Roll back to this version", permissions=["rollback"])
    def rollback(self, request: HttpRequest, queryset: Any) -> HttpResponse | None:
        raise NotImplementedError

    def has_rollback_permission(self, request: HttpRequest) -> bool:
        raise NotImplementedError


@admin.register(Snapshot)
class SnapshotAdmin(ReadOnlyAdmin):
    pass
