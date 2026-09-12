import json
from collections.abc import Callable, Sequence
from typing import Any

from django.db.models import Count
from django.http import HttpResponse
from django.utils.dateparse import parse_datetime
from django.utils.module_loading import import_string
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from tunables.api import problems
from tunables.api.actors import request_id, resolve_actor
from tunables.api.serializers import ChangeSetDetailSerializer, ChangeSetSerializer, ChangesRequestSerializer
from tunables.catalogue import Catalogue, Group, Tunable
from tunables.changes import Change
from tunables.conf import settings
from tunables.errors import CatalogueOutOfSync, FieldWarning
from tunables.models import ChangeSet, Snapshot, State
from tunables.registry import get_catalogue
from tunables.schema import describe_group, validator_description
from tunables.services import apply_changeset, latest_snapshot, validate
from tunables.sync import is_synced


def _instantiate(entries: Sequence[Any]) -> list[Any]:
    return [(import_string(entry) if isinstance(entry, str) else entry)() for entry in entries]


class TunablesAPIView(APIView):
    """Shared behaviour: settings-driven auth, sync check, X-Tunables-Version header, problem responses."""

    def get_authenticators(self) -> list[BaseAuthentication]:
        configured = settings.API_AUTHENTICATION_CLASSES
        return super().get_authenticators() if configured is None else _instantiate(configured)

    def get_permissions(self) -> Sequence[Any]:
        configured = settings.API_PERMISSION_CLASSES
        return super().get_permissions() if configured is None else _instantiate(configured)

    def get_exception_handler(self) -> Callable[..., Response | None]:
        return problems.exception_handler

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        if not is_synced():
            raise CatalogueOutOfSync("catalogue changed since the last sync; run tunables_sync")

    def finalize_response(self, request: Request, response: Response, *args: Any, **kwargs: Any) -> Response:
        response = super().finalize_response(request, response, *args, **kwargs)
        version = State.objects.filter(pk=1).values_list("current_version", flat=True).first()
        if version is not None:
            response["X-Tunables-Version"] = str(version)
        return response


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


def _expected_version(request: Request) -> int | None:
    header = request.headers.get("If-Match")
    if header is None or header.strip() == "*":
        return None
    tag = header.strip()
    if len(tag) >= 2 and tag[0] == tag[-1] == '"':
        tag = tag[1:-1]
    if not tag.isdigit():
        raise ValidationError({"If-Match": 'expected a quoted version number, for example "42"'})
    return int(tag)


def _parsed_changes(request: Request) -> tuple[list[Change], str, bool]:
    serializer = ChangesRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    changes = [Change(item["key"], item.get("value"), item["reset"]) for item in data["changes"]]
    return changes, data["reason"], data["dry_run"]


def _write(
    request: Request,
    changes: Sequence[Change],
    *,
    source: str,
    reason: str,
    dry_run: bool = False,
    restores_version: int | None = None,
    extra_warnings: Sequence[FieldWarning] = (),
) -> Response:
    expected_version = _expected_version(request)
    if dry_run:
        warnings = [*extra_warnings, *validate(changes)]
        return Response({"valid": True, "warnings": [problems.describe(w) for w in warnings]})
    result = apply_changeset(
        changes,
        actor=resolve_actor(request),
        source=source,
        reason=reason,
        expected_version=expected_version,
        request_id=request_id(request),
        restores_version=restores_version,
    )
    changeset = ChangeSet.objects.annotate(item_count=Count("items")).get(pk=result.changeset.pk)
    warnings = [*extra_warnings, *result.warnings]
    body = {
        "version": result.version,
        "changeset": ChangeSetDetailSerializer(changeset).data,
        "warnings": [problems.describe(w) for w in warnings],
    }
    return Response(body, status=201)


def _with_etag(request: Request, version: int, body: Any) -> Response:
    etag = f'"{version}"'
    offered = [tag.strip() for tag in request.headers.get("If-None-Match", "").split(",")]
    response = Response(status=304) if etag in offered else Response(body)
    response["ETag"] = etag
    return response


class Values(TunablesAPIView):
    def get(self, request: Request) -> Response:
        document = latest_snapshot().document
        body = {key: document[key] for key in ("version", "groups", "overridden")}
        return _with_etag(request, document["version"], body)


class GroupValues(TunablesAPIView):
    def patch(self, request: Request, group: str) -> Response:
        raise NotImplementedError

    def get(self, request: Request, group: str) -> Response:
        _group(get_catalogue(), group)
        document = latest_snapshot().document
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
    def post(self, request: Request) -> Response:
        changes, reason, dry_run = _parsed_changes(request)
        return _write(request, changes, source="api", reason=reason, dry_run=dry_run)

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
    def get(self, request: Request, version: int) -> Response:
        changeset = ChangeSet.objects.annotate(item_count=Count("items")).filter(version=version).first()
        if changeset is None:
            raise NotFound(f"unknown version {version}")
        return Response(ChangeSetDetailSerializer(changeset).data)


class LatestSnapshot(TunablesAPIView):
    def get(self, request: Request) -> Response:
        snapshot = latest_snapshot()
        return _with_etag(request, snapshot.version, snapshot.document)


class SnapshotDetail(TunablesAPIView):
    def get(self, request: Request, version: int) -> Response:
        snapshot = Snapshot.objects.filter(version=version).first()
        if snapshot is None:
            raise NotFound(f"unknown version {version}")
        return _with_etag(request, snapshot.version, snapshot.document)


class Export(TunablesAPIView):
    def get(self, request: Request) -> HttpResponse:
        snapshot = latest_snapshot()
        response = HttpResponse(json.dumps(snapshot.document, indent=2), content_type="application/json")
        response["Content-Disposition"] = f'attachment; filename="tunables-v{snapshot.version}.json"'
        return response


class Validate(TunablesAPIView):
    def post(self, request: Request) -> Response:
        changes, reason, _ = _parsed_changes(request)
        return _write(request, changes, source="api", reason=reason, dry_run=True)


class Rollback(TunablesAPIView):
    def post(self, request: Request) -> Response:
        raise NotImplementedError


class Import(TunablesAPIView):
    def post(self, request: Request) -> Response:
        raise NotImplementedError
