import json
from typing import TYPE_CHECKING, Any

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, QuerySet
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import URLPattern, path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from tunables.access import check_editable, editable_groups
from tunables.admin.forms import GroupForm, build_group_form
from tunables.changes import Actor
from tunables.errors import (
    CatalogueOutOfSync,
    FieldError,
    GroupNotEditable,
    NothingToChange,
    ValidationFailed,
    VersionConflict,
)
from tunables.management.base import describe
from tunables.models import ChangeItem, ChangeSet, Snapshot, TunableDefinition
from tunables.registry import get_catalogue
from tunables.schema import validator_description
from tunables.services import apply_changeset, latest_snapshot, rollback, rollback_changes, rule_violations
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
        editable = editable_groups(request)
        groups = [
            {
                "name": group.name,
                "title": str(group.title) or group.name,
                "description": str(group.description),
                "count": len(group.tunables),
                "validators": [validator_description(v) for v in group.validators],
                "edit_url": reverse("admin:tunables_group_edit", args=[group.name]),
                "can_edit": self.has_write_permission(request) and (editable is None or group.name in editable),
            }
            for group in get_catalogue().groups.values()
        ]
        context = {
            **self.admin_site.each_context(request),
            **(extra_context or {}),
            "title": _("Tunables"),
            "opts": self.model._meta,
            "groups": groups,
            "validators": [validator_description(v) for v in get_catalogue().validators],
            "violations": [describe(v) for v in rule_violations()] if is_synced() else [],
            "synced": is_synced(),
        }
        return TemplateResponse(request, "tunables/admin/group_index.html", context)

    def edit_group(self, request: HttpRequest, group: str) -> HttpResponse:
        if not self.has_write_permission(request):
            raise PermissionDenied
        catalogue = get_catalogue()
        if group not in catalogue.groups:
            raise Http404(f"unknown group {group!r}")
        editable = editable_groups(request)
        if editable is not None and group not in editable:
            raise PermissionDenied
        index = reverse("admin:tunables_tunabledefinition_changelist")
        if not is_synced():
            messages.error(request, _("The catalogue in code differs from the database. Run tunables_sync first."))
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
                form.add_error(None, _("Nothing changed."))
            except VersionConflict as conflict:
                messages.warning(
                    request,
                    _(
                        "The values changed while you were editing: version %(expected)s is now version %(actual)s. "
                        "The form shows the current values; review and submit again."
                    )
                    % {"expected": conflict.expected, "actual": conflict.actual},
                )
                form = self._form_class(found)()
            else:
                messages.success(
                    request,
                    _("Saved version %(version)s of %(group)s.")
                    % {"version": result.version, "group": str(found.title) or found.name},
                )
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
            "title": _("Edit %(group)s") % {"group": str(found.title) or found.name},
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
    list_display = ["version", "created_at", "actor", "source", "reason", "item_count"]
    list_filter = ["source", "actor_source"]
    search_fields = ["reason", "actor", "items__key"]
    inlines = [ChangeItemInline]
    actions = ["rollback"]

    def get_queryset(self, request: HttpRequest) -> QuerySet[ChangeSet]:
        queryset: QuerySet[ChangeSet] = super().get_queryset(request)
        return queryset.annotate(item_count=Count("items"))

    @admin.display(description=gettext_lazy("Items"), ordering="item_count")
    def item_count(self, changeset: ChangeSet) -> int:
        return int(getattr(changeset, "item_count", 0))

    def has_rollback_permission(self, request: HttpRequest) -> bool:
        return bool(request.user.has_perm("tunables.add_changeset"))

    @admin.action(description=gettext_lazy("Roll back to this version"), permissions=["rollback"])
    def rollback(self, request: HttpRequest, queryset: QuerySet[ChangeSet]) -> HttpResponse | None:
        if queryset.count() != 1:
            self.message_user(request, _("Select exactly one change set to roll back to."), messages.ERROR)
            return None
        changeset = queryset.get()
        try:
            check_editable(request, rollback_changes(changeset.version))
        except GroupNotEditable as refused:
            self.message_user(request, _("Cannot roll back: %(reason)s") % {"reason": refused}, messages.ERROR)
            return None
        error = ""
        if request.POST.get("confirm"):
            reason = request.POST.get("reason", "").strip()
            if reason:
                self._roll_back(request, changeset.version, reason)
                return None
            error = _("A reason is required.")
        context = {
            **self.admin_site.each_context(request),
            "title": _("Roll back to version %(version)s") % {"version": changeset.version},
            "opts": self.model._meta,
            "changeset": changeset,
            "error": error,
        }
        return TemplateResponse(request, "tunables/admin/rollback_confirm.html", context)

    def _roll_back(self, request: HttpRequest, version: int, reason: str) -> None:
        actor = Actor(request.user.get_username(), "verified")
        try:
            result = rollback(version, actor=actor, reason=reason)
        except NothingToChange:
            self.message_user(
                request,
                _("Nothing to change: the values already match version %(version)s.") % {"version": version},
                messages.WARNING,
            )
        except (ValidationFailed, CatalogueOutOfSync) as failure:
            self.message_user(request, _("Rollback failed: %(reason)s") % {"reason": failure}, messages.ERROR)
        else:
            self.message_user(
                request,
                _("Rolled back to version %(version)s as version %(new)s.")
                % {"version": version, "new": result.version},
            )


@admin.register(Snapshot)
class SnapshotAdmin(ReadOnlyAdmin):
    list_display = ["version", "created_at", "catalogue_version", "changeset"]
    fields = ["version", "changeset", "created_at", "format_version", "catalogue_version", "pretty_document"]
    readonly_fields = ["pretty_document"]

    @admin.display(description=gettext_lazy("Document"))
    def pretty_document(self, snapshot: Snapshot) -> str:
        return format_html("<pre>{}</pre>", json.dumps(snapshot.document, indent=2))
