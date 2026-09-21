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

from tunables.access import check_editable, check_group_editable, editable_groups
from tunables.admin.forms import DefinitionTagsForm, GroupForm, build_group_form
from tunables.catalogue import validator_description
from tunables.changes import Actor
from tunables.errors import (
    CatalogueOutOfSync,
    FieldError,
    GroupNotEditable,
    NothingToChange,
    UnknownVersion,
    ValidationFailed,
    VersionConflict,
    format_error,
)
from tunables.models import ChangeItem, ChangeSet, Snapshot, Tag, TunableDefinition, TunableDefinitionTag
from tunables.registry import get_catalogue
from tunables.search import match_definitions, tags_by_key
from tunables.services import (
    apply_changeset,
    diff_versions,
    latest_snapshot,
    rollback,
    rollback_changes,
    rule_violations,
)
from tunables.sync import is_synced
from tunables.tags import set_manual_tags

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
        view = self.admin_site.admin_view
        return [
            path("edit/<str:group>/", view(self.edit_group), name="tunables_group_edit"),
            path("definitions/", view(self.definitions), name="tunables_definitions"),
            path("definitions/<str:key>/tags/", view(self.definition_tags), name="tunables_definition_tags"),
            *super().get_urls(),
        ]

    def definitions(self, request: HttpRequest) -> HttpResponse:
        """Browse every definition with its category, group and tags; filter and search."""
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied
        catalogue = get_catalogue()
        category = request.GET.get("category") or None
        if category is not None and category not in catalogue.categories:
            raise Http404(f"unknown category {category!r}")
        group = request.GET.get("group") or None
        if group is not None and group not in catalogue.groups:
            raise Http404(f"unknown group {group!r}")
        matches = match_definitions(
            catalogue,
            tags_by_key(),
            category=category,
            group=group,
            wanted_tags=request.GET.getlist("tag"),
            query=request.GET.get("q", ""),
        )
        can_tag = self.has_tag_permission(request)
        editable = editable_groups(request)
        rows = [
            {
                "key": f"{g.name}.{tunable.name}",
                "title": str(tunable.title) or tunable.name,
                "category": g.category,
                "group": g.name,
                "type": tunable.type.name,
                "tags": sorted(own_tags),
                "tags_url": reverse("admin:tunables_definition_tags", args=[f"{g.name}.{tunable.name}"])
                if can_tag and (editable is None or g.name in editable)
                else "",
            }
            for g, tunable, own_tags in matches
        ]
        context = {
            **self.admin_site.each_context(request),
            "title": _("Definitions"),
            "opts": self.model._meta,
            "rows": rows,
            "categories": list(catalogue.categories.values()),
            "groups": list(catalogue.groups.values()),
            "tag_names": sorted(Tag.objects.values_list("name", flat=True)),
            "filters": {k: request.GET.get(k, "") for k in ("category", "group", "tag", "q")},
            "index_url": reverse("admin:tunables_tunabledefinition_changelist"),
        }
        return TemplateResponse(request, "tunables/admin/definitions.html", context)

    def definition_tags(self, request: HttpRequest, key: str) -> HttpResponse:
        """Edit the manual tags of one definition; seeded tags are shown read-only."""
        if not self.has_tag_permission(request):
            raise PermissionDenied
        catalogue = get_catalogue()
        if key not in set(catalogue.keys()):
            raise Http404(f"unknown tunable {key!r}")
        try:
            check_group_editable(request, key.partition(".")[0])
        except GroupNotEditable as refused:
            raise PermissionDenied(str(refused)) from refused
        rows = TunableDefinitionTag.objects.filter(definition__key=key).select_related("tag")
        seeded = sorted(row.tag.name for row in rows if row.seeded)
        manual = sorted(row.tag.name for row in rows if not row.seeded)
        definitions_url = reverse("admin:tunables_definitions")
        if request.method == "POST":
            form = DefinitionTagsForm(request.POST)
            if form.is_valid():
                set_manual_tags(key, form.cleaned_data["tags"])
                messages.success(request, _("Saved the tags of %(key)s.") % {"key": key})
                return HttpResponseRedirect(definitions_url)
        else:
            form = DefinitionTagsForm(initial={"tags": ", ".join(manual)})
        context = {
            **self.admin_site.each_context(request),
            "title": _("Tags of %(key)s") % {"key": key},
            "opts": self.model._meta,
            "key": key,
            "tunable": catalogue.get(key),
            "seeded": seeded,
            "form": form,
            "definitions_url": definitions_url,
        }
        return TemplateResponse(request, "tunables/admin/definition_tags.html", context)

    def has_write_permission(self, request: HttpRequest) -> bool:
        return bool(request.user.has_perm("tunables.add_changeset"))

    def has_tag_permission(self, request: HttpRequest) -> bool:
        """Labelling is metadata, so it takes the Tag permission rather than the change set one."""
        return bool(request.user.has_perm("tunables.change_tag"))

    def changelist_view(self, request: HttpRequest, extra_context: dict[str, Any] | None = None) -> HttpResponse:
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied
        catalogue = get_catalogue()
        editable = editable_groups(request)

        def row(group: Any) -> dict[str, Any]:
            return {
                "name": group.name,
                "title": str(group.title) or group.name,
                "description": str(group.description),
                "count": len(group.tunables),
                "validators": [validator_description(v) for v in group.validators],
                "edit_url": reverse("admin:tunables_group_edit", args=[group.name]),
                "can_edit": self.has_write_permission(request) and (editable is None or group.name in editable),
            }

        categories = [
            {
                "name": category.name,
                "title": str(category.title) or category.name,
                "description": str(category.description),
                "groups": [row(group) for group in catalogue.groups_in(category.name)],
            }
            for category in catalogue.categories.values()
            if catalogue.groups_in(category.name)
        ]
        context = {
            **self.admin_site.each_context(request),
            **(extra_context or {}),
            "title": _("Tunables"),
            "opts": self.model._meta,
            "categories": categories,
            "groups": [group for category in categories for group in category["groups"]],
            "definitions_url": reverse("admin:tunables_definitions"),
            "validators": [validator_description(v) for v in catalogue.validators],
            "violations": [format_error(v) for v in rule_violations()] if is_synced() else [],
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
                moved = self._moved_names(found, conflict.expected, conflict.actual)
                context = {"expected": conflict.expected, "actual": conflict.actual, "names": ", ".join(moved)}
                if moved:
                    text = _(
                        "The values changed while you were editing: version %(expected)s is now version "
                        "%(actual)s, and these changed: %(names)s. Your input is kept; review and submit again."
                    )
                else:
                    text = _(
                        "The values changed while you were editing: version %(expected)s is now version "
                        "%(actual)s. Your input is kept; review and submit again."
                    )
                messages.warning(request, text % context)
                data = request.POST.copy()
                data["expected_version"] = str(conflict.actual)
                form = self._form_class(found)(data)
                form.is_valid()
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
    def _moved_names(group: Any, expected: int, actual: int) -> list[str]:
        """Tunable names of this group that changed between two versions, for the conflict message."""
        prefix = f"{group.name}."
        try:
            entries = diff_versions(expected, actual)
        except UnknownVersion:
            return []
        return [entry.key.removeprefix(prefix) for entry in entries if entry.key.startswith(prefix)]

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


@admin.register(Tag)
class TagAdmin(ModelAdmin):
    """Tags by hand: create and edit; seeded tags cannot be deleted."""

    list_display = ["name", "description", "from_catalogue", "definition_count"]
    readonly_fields = ["from_catalogue", "created_at"]
    search_fields = ["name", "description"]
    actions = None

    def get_queryset(self, request: HttpRequest) -> QuerySet[Tag]:
        queryset: QuerySet[Tag] = super().get_queryset(request)
        return queryset.annotate(definition_count=Count("definitions"))

    @admin.display(description=gettext_lazy("Definitions"), ordering="definition_count")
    def definition_count(self, tag: Tag) -> int:
        return int(getattr(tag, "definition_count", 0))

    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> list[str]:
        # A seeded tag's name belongs to the code; renaming it would only make sync recreate the original.
        if obj is not None and obj.from_catalogue:
            return [*self.readonly_fields, "name"]
        return list(self.readonly_fields)

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        if obj is not None and obj.from_catalogue:
            return False
        return bool(super().has_delete_permission(request, obj))


@admin.register(Snapshot)
class SnapshotAdmin(ReadOnlyAdmin):
    list_display = ["version", "created_at", "catalogue_version", "changeset"]
    fields = ["version", "changeset", "created_at", "format_version", "catalogue_version", "pretty_document"]
    readonly_fields = ["pretty_document"]

    @admin.display(description=gettext_lazy("Document"))
    def pretty_document(self, snapshot: Snapshot) -> str:
        return format_html("<pre>{}</pre>", json.dumps(snapshot.document, indent=2))
