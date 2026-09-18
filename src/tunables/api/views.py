import json
from collections.abc import Mapping
from typing import Any

from django.db.models import Count
from django.http import HttpResponse
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.request import Request
from rest_framework.response import Response

from tunables.api import problems
from tunables.api.base import TunablesAPIView
from tunables.api.serializers import ChangeSetDetailSerializer, ChangeSetSerializer
from tunables.api.writes import parsed_changes, write
from tunables.catalogue import Catalogue, Group, Tunable
from tunables.changes import Change
from tunables.conf import settings
from tunables.errors import UnknownVersion
from tunables.models import ChangeSet, PublisherState, Snapshot, State
from tunables.registry import get_catalogue
from tunables.schema import describe_group, document_schema, validator_description
from tunables.services import diff_versions, latest_snapshot, rule_violations


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
        "validators": [validator_description(validator) for validator in group.validators],
    }


def _definition(group: Group, tunable: Tunable) -> dict[str, Any]:
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
    }


class GroupList(TunablesAPIView):
    def get(self, request: Request) -> Response:
        return Response(
            [{**_summary(group), "tunable_count": len(group.tunables)} for group in get_catalogue().groups.values()]
        )


class GroupDetail(TunablesAPIView):
    def get(self, request: Request, group: str) -> Response:
        found = _group(get_catalogue(), group)
        return Response(
            {
                **_summary(found),
                "ui": dict(found.ui),
                "metadata": dict(found.metadata),
                "definitions": [_definition(found, tunable) for tunable in found.tunables],
            }
        )


class GroupSchema(TunablesAPIView):
    def get(self, request: Request, group: str) -> Response:
        catalogue = get_catalogue()
        return Response(describe_group(catalogue, _group(catalogue, group)))


class SchemaList(TunablesAPIView):
    def get(self, request: Request) -> Response:
        catalogue = get_catalogue()
        return Response({name: describe_group(catalogue, group) for name, group in catalogue.groups.items()})


class DefinitionList(TunablesAPIView):
    def get(self, request: Request) -> Response:
        catalogue = get_catalogue()
        return Response([_definition(group, t) for group in catalogue.groups.values() for t in group.tunables])


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
        return Response(document_schema(get_catalogue()))
