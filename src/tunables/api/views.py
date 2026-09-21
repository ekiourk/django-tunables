import json
from collections.abc import Mapping, Sequence
from typing import Any

from django.db.models import Count
from django.http import HttpResponse
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.request import Request
from rest_framework.response import Response

from tunables.api import problems
from tunables.api.base import TunablesAPIView
from tunables.api.serializers import (
    ChangeSetDetailSerializer,
    ChangeSetSerializer,
    TagDescriptionSerializer,
    TagRequestSerializer,
)
from tunables.api.writes import parsed_changes, write
from tunables.catalogue import Catalogue, Category, Group, Tunable
from tunables.changes import Change
from tunables.conf import settings
from tunables.errors import UnknownVersion
from tunables.models import ChangeSet, PublisherState, Snapshot, State, Tag
from tunables.registry import get_catalogue
from tunables.schema import describe_group, document_schema, validator_description
from tunables.search import match_definitions, require_tags, tags_by_key
from tunables.services import diff_versions, latest_snapshot, rule_violations
from tunables.tags import create_tag, delete_tag, update_tag


def _group(catalogue: Catalogue, name: str) -> Group:
    try:
        return catalogue.groups[name]
    except KeyError:
        raise NotFound(f"unknown group {name!r}") from None


def _summary(group: Group) -> dict[str, Any]:
    return {
        "name": group.name,
        "title": str(group.title),
        "description": str(group.description),
        "order": group.order,
        "category": group.category,
        "validators": [validator_description(validator) for validator in group.validators],
    }


def _definition(group: Group, tunable: Tunable, tags: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "key": f"{group.name}.{tunable.name}",
        "group": group.name,
        "name": tunable.name,
        "type": tunable.type.describe(),
        "default": tunable.type.to_json(tunable.default),
        "title": str(tunable.title),
        "description": str(tunable.description),
        "unit": tunable.unit,
        "ui": dict(tunable.ui),
        "metadata": dict(tunable.metadata),
        "deprecated": tunable.deprecated,
        "category": group.category,
        "tags": sorted(tags),
    }


def _category(catalogue: Catalogue, name: str) -> Category:
    try:
        return catalogue.categories[name]
    except KeyError:
        raise NotFound(f"unknown category {name!r}") from None


INDEX = {
    "categories": "categories",
    "groups": "groups",
    "definitions": "definitions",
    "values": "values",
    "tags": "tags",
    "changesets": "changesets",
    "snapshots": "snapshot-latest",
    "schema": "schema",
    "status": "status",
}


class Index(TunablesAPIView):
    """The endpoint map, so the mount point and the browsable API have a starting point."""

    reads_need_sync = False

    def get(self, request: Request) -> Response:
        return Response(
            {name: request.build_absolute_uri(reverse(f"tunables:{route}")) for name, route in INDEX.items()}
        )


class CategoryList(TunablesAPIView):
    def get(self, request: Request) -> Response:
        catalogue = get_catalogue()
        return Response(
            [
                {
                    "name": category.name,
                    "title": str(category.title),
                    "description": str(category.description),
                    "order": category.order,
                    "groups": [group.name for group in catalogue.groups_in(category.name)],
                }
                for category in catalogue.categories.values()
            ]
        )


def _tag_summary(tag: Tag) -> dict[str, Any]:
    return {
        "name": tag.name,
        "description": tag.description,
        "from_catalogue": tag.from_catalogue,
        "definition_count": int(getattr(tag, "definition_count", 0)),
    }


class TagList(TunablesAPIView):
    reads_need_sync = False

    def post(self, request: Request) -> Response:
        serializer = TagRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tag = create_tag(serializer.validated_data["name"], serializer.validated_data["description"])
        return Response(_tag_summary(tag), status=201)

    def get(self, request: Request) -> Response:
        tags = Tag.objects.annotate(definition_count=Count("definitions")).order_by("name")
        return Response([_tag_summary(tag) for tag in tags])


class TagDetail(TunablesAPIView):
    reads_need_sync = False

    def patch(self, request: Request, name: str) -> Response:
        serializer = TagDescriptionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            update_tag(name, serializer.validated_data["description"])
        except Tag.DoesNotExist:
            raise NotFound(f"unknown tag {name!r}") from None
        tag = Tag.objects.annotate(definition_count=Count("definitions")).get(name=name)
        return Response(_tag_summary(tag))

    def delete(self, request: Request, name: str) -> Response:
        try:
            delete_tag(name)
        except Tag.DoesNotExist:
            raise NotFound(f"unknown tag {name!r}") from None
        return Response(status=204)

    def get(self, request: Request, name: str) -> Response:
        tag = Tag.objects.annotate(definition_count=Count("definitions")).filter(name=name).first()
        if tag is None:
            raise NotFound(f"unknown tag {name!r}")
        tagged = set(tag.definitions.values_list("key", flat=True))
        ordered_keys = get_catalogue().keys()
        keys = [key for key in ordered_keys if key in tagged]
        return Response({**_tag_summary(tag), "definitions": keys})


class GroupList(TunablesAPIView):
    def get(self, request: Request) -> Response:
        catalogue = get_catalogue()
        groups: Sequence[Group] = list(catalogue.groups.values())
        if category := request.query_params.get("category"):
            groups = catalogue.groups_in(_category(catalogue, category).name)
        return Response([{**_summary(group), "tunable_count": len(group.tunables)} for group in groups])


class GroupDetail(TunablesAPIView):
    def get(self, request: Request, group: str) -> Response:
        found = _group(get_catalogue(), group)
        tags = tags_by_key()
        return Response(
            {
                **_summary(found),
                "ui": dict(found.ui),
                "metadata": dict(found.metadata),
                "definitions": [
                    _definition(found, tunable, tags.get(f"{found.name}.{tunable.name}", []))
                    for tunable in found.tunables
                ],
            }
        )


def _tagged_names(catalogue: Catalogue, tags: dict[str, list[str]], wanted: list[str], group: Group) -> set[str] | None:
    """Names of the group's tunables carrying every wanted tag, or None when nothing is wanted."""
    if not wanted:
        return None
    matches = match_definitions(catalogue, tags, group=group.name, wanted_tags=wanted)
    return {tunable.name for _, tunable, _ in matches}


class GroupSchema(TunablesAPIView):
    def get(self, request: Request, group: str) -> Response:
        catalogue = get_catalogue()
        found = _group(catalogue, group)
        wanted = request.query_params.getlist("tag")
        require_tags(wanted)
        tags = tags_by_key()
        names = _tagged_names(catalogue, tags, wanted, found)
        if names is not None and not names:
            raise NotFound(f"no tunable in group {group!r} carries tags {sorted(wanted)!r}")
        return Response(describe_group(catalogue, found, tags=tags, names=names))


class SchemaList(TunablesAPIView):
    def get(self, request: Request) -> Response:
        catalogue = get_catalogue()
        wanted = request.query_params.getlist("tag")
        require_tags(wanted)
        tags = tags_by_key()
        body = {}
        for name, group in catalogue.groups.items():
            names = _tagged_names(catalogue, tags, wanted, group)
            if names is None or names:
                body[name] = describe_group(catalogue, group, tags=tags, names=names)
        return Response(body)


class DefinitionList(TunablesAPIView):
    def get(self, request: Request) -> Response:
        catalogue = get_catalogue()
        params = request.query_params
        category = params.get("category") or None
        if category is not None:
            _category(catalogue, category)
        group = params.get("group") or None
        if group is not None:
            _group(catalogue, group)
        require_tags(params.getlist("tag"))
        matches = match_definitions(
            catalogue,
            tags_by_key(),
            category=category,
            group=group,
            wanted_tags=params.getlist("tag"),
            query=params.get("q", ""),
        )
        return Response([_definition(g, tunable, own_tags) for g, tunable, own_tags in matches])


def _with_etag(request: Request, version: int, body: Any) -> Response:
    etag = f'"{version}"'
    offered = [tag.strip() for tag in request.headers.get("If-None-Match", "").split(",")]
    response = Response(status=304) if etag in offered else Response(body)
    response["ETag"] = etag
    return response


class Values(TunablesAPIView):
    reads_need_sync = False

    def get(self, request: Request) -> Response:
        document = latest_snapshot().document
        body = {key: document[key] for key in ("version", "groups", "overridden")}
        return _with_etag(request, document["version"], body)


class GroupValues(TunablesAPIView):
    reads_need_sync = False

    def patch(self, request: Request, group: str) -> Response:
        _group(get_catalogue(), group)
        if not isinstance(request.data, Mapping):
            raise ValidationError("expected an object mapping tunable names to values")
        changes = [
            Change(f"{group}.{name}", reset=True) if value is None else Change(f"{group}.{name}", value)
            for name, value in request.data.items()
        ]
        return write(request, changes, source="api", reason=request.headers.get(settings.REASON_HEADER, ""))

    def get(self, request: Request, group: str) -> Response:
        document = latest_snapshot().document
        if group not in document["groups"]:
            raise NotFound(f"unknown group {group!r}")
        prefix = f"{group}."
        body = {
            "version": document["version"],
            "values": document["groups"][group],
            "overridden": [key.removeprefix(prefix) for key in document["overridden"] if key.startswith(prefix)],
        }
        return _with_etag(request, document["version"], body)


class ChangeSetPagination(PageNumberPagination):
    def get_page_size(self, request: Request) -> int:
        return int(settings.PAGE_SIZE)


class ChangeSetList(TunablesAPIView):
    reads_need_sync = False

    def post(self, request: Request) -> Response:
        changes, reason, dry_run, metadata = parsed_changes(request)
        return write(request, changes, source="api", reason=reason, dry_run=dry_run, metadata=metadata)

    def get(self, request: Request) -> Response:
        matching = ChangeSet.objects.all()
        if group := request.query_params.get("group"):
            matching = matching.filter(items__key__startswith=f"{group}.")
        if key := request.query_params.get("key"):
            matching = matching.filter(items__key=key)
        if actor := request.query_params.get("actor"):
            matching = matching.filter(actor=actor)
        if since := request.query_params.get("since"):
            parsed = parse_datetime(since)
            if parsed is None:
                raise ValidationError({"since": "expected an ISO 8601 datetime"})
            matching = matching.filter(created_at__gte=parsed)
        queryset = ChangeSet.objects.filter(pk__in=matching.values("pk")).annotate(item_count=Count("items"))
        paginator = ChangeSetPagination()
        page = paginator.paginate_queryset(queryset.order_by("-version"), request, view=self)
        return paginator.get_paginated_response(ChangeSetSerializer(page, many=True).data)


class ChangeSetDetail(TunablesAPIView):
    reads_need_sync = False

    def get(self, request: Request, version: int) -> Response:
        changeset = ChangeSet.objects.annotate(item_count=Count("items")).filter(version=version).first()
        if changeset is None:
            raise NotFound(f"unknown version {version}")
        return Response(ChangeSetDetailSerializer(changeset).data)


class LatestSnapshot(TunablesAPIView):
    reads_need_sync = False

    def get(self, request: Request) -> Response:
        snapshot = latest_snapshot()
        return _with_etag(request, snapshot.version, snapshot.document)


class SnapshotDetail(TunablesAPIView):
    reads_need_sync = False

    def get(self, request: Request, version: int) -> Response:
        snapshot = Snapshot.objects.filter(version=version).first()
        if snapshot is None:
            raise NotFound(f"unknown version {version}")
        return _with_etag(request, snapshot.version, snapshot.document)


class Export(TunablesAPIView):
    reads_need_sync = False

    def get(self, request: Request) -> HttpResponse:
        snapshot = latest_snapshot()
        response = HttpResponse(json.dumps(snapshot.document, indent=2), content_type="application/json")
        response["Content-Disposition"] = f'attachment; filename="tunables-v{snapshot.version}.json"'
        return response


class Diff(TunablesAPIView):
    reads_need_sync = False

    def get(self, request: Request) -> Response:
        versions = {}
        for name in ("from", "to"):
            raw = request.query_params.get(name, "")
            if not raw.isdigit():
                raise ValidationError({name: "expected a version number"})
            versions[name] = int(raw)
        try:
            entries = diff_versions(versions["from"], versions["to"])
        except UnknownVersion as error:
            raise NotFound(str(error)) from error
        changes = [{"key": entry.key, "old": entry.old, "new": entry.new} for entry in entries]
        return Response({"from": versions["from"], "to": versions["to"], "changes": changes})


class Status(TunablesAPIView):
    reads_need_sync = False

    def get(self, request: Request) -> Response:
        code_version = get_catalogue().version
        state = State.objects.filter(pk=1).first()
        return Response(
            {
                "synced": state is not None and state.catalogue_version == code_version,
                "version": None if state is None else state.current_version,
                "catalogue_version": None if state is None else state.catalogue_version,
                "code_catalogue_version": code_version,
                "validators": [validator_description(v) for v in get_catalogue().validators],
                "publishers": [
                    {
                        "publisher": row.publisher,
                        "last_version": row.last_version,
                        "last_published_at": row.last_published_at,
                        "last_error": row.last_error,
                    }
                    for row in PublisherState.objects.order_by("publisher")
                ],
                "rule_violations": [problems.describe(v) for v in rule_violations()] if state is not None else [],
            }
        )


class SnapshotSchema(TunablesAPIView):
    def get(self, request: Request) -> Response:
        return Response(document_schema(get_catalogue(), tags=tags_by_key()))
