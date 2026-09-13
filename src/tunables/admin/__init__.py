from typing import TYPE_CHECKING, Any

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import URLPattern, path, reverse

from tunables.admin.forms import GroupForm, build_group_form
from tunables.changes import Actor
from tunables.errors import FieldError, NothingToChange, ValidationFailed, VersionConflict
from tunables.models import ChangeItem, ChangeSet, Snapshot, TunableDefinition
from tunables.registry import get_catalogue
from tunables.schema import validator_description
from tunables.services import apply_changeset, latest_snapshot
from tunables.sync import is_synced

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

    def has_write_permission(self, request: HttpRequest) -> bool:
        return bool(request.user.has_perm("tunables.add_changeset"))

    def changelist_view(self, request: HttpRequest, extra_context: dict[str, Any] | None = None) -> HttpResponse:
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied
        groups = [
            {
                "name": group.name,
                "title": str(group.title) or group.name,
                "description": str(group.description),
                "count": len(group.tunables),
                "validators": [validator_description(v) for v in group.validators],
                "edit_url": reverse("admin:tunables_group_edit", args=[group.name]),
            }
            for group in get_catalogue().groups.values()
        ]
        context = {
            **self.admin_site.each_context(request),
            **(extra_context or {}),
            "title": "Tunables",
            "opts": self.model._meta,
            "groups": groups,
            "synced": is_synced(),
            "can_edit": self.has_write_permission(request),
        }
        return TemplateResponse(request, "tunables/admin/group_index.html", context)

    def edit_group(self, request: HttpRequest, group: str) -> HttpResponse:
        if not self.has_write_permission(request):
            raise PermissionDenied
        catalogue = get_catalogue()
        if group not in catalogue.groups:
            raise Http404(f"unknown group {group!r}")
        index = reverse("admin:tunables_tunabledefinition_changelist")
        if not is_synced():
            messages.error(request, "The catalogue in code differs from the database. Run tunables_sync first.")
            return HttpResponseRedirect(index)
        found = catalogue.groups[group]
        form_class = self._form_class(found)
        form = form_class(request.POST) if request.method == "POST" else form_class()
        if request.method == "POST" and form.is_valid():
            try:
                result = apply_changeset(
                    form.changes(),
                    actor=Actor(request.user.get_username(), "verified"),
                    source="admin",
                    reason=form.cleaned_data["reason"],
                    expected_version=form.cleaned_data["expected_version"],
                )
            except ValidationFailed as error:
                for item in error.errors:
                    field = item.key.partition(".")[2] if isinstance(item, FieldError) else None
                    form.add_error(field if field in form.fields else None, item.message)
            except NothingToChange:
                form.add_error(None, "Nothing changed.")
            except VersionConflict as conflict:
                messages.warning(
                    request,
                    f"The values changed while you were editing: version {conflict.expected} is now "
                    f"version {conflict.actual}. The form shows the current values; review and submit again.",
                )
                form = self._form_class(found)()
            else:
                messages.success(request, f"Saved version {result.version} of {str(found.title) or found.name}.")
                return HttpResponseRedirect(index)
        rows = [
            (
                tunable,
                form[tunable.name],
                form[f"reset_{tunable.name}"] if f"reset_{tunable.name}" in form.fields else None,
            )
            for tunable in found.tunables
        ]
        context = {
            **self.admin_site.each_context(request),
            "title": f"Edit {str(found.title) or found.name}",
            "opts": self.model._meta,
            "group": found,
            "form": form,
            "rows": rows,
            "index_url": index,
        }
        return TemplateResponse(request, "tunables/admin/group_edit.html", context)

    @staticmethod
    def _form_class(group: Any) -> type[GroupForm]:
        document = latest_snapshot().document
        prefix = f"{group.name}."
        overridden = {key.removeprefix(prefix) for key in document["overridden"] if key.startswith(prefix)}
        return build_group_form(group, document["groups"][group.name], overridden, document["version"])


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
