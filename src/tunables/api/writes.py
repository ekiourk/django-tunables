from collections.abc import Mapping, Sequence

from django.db.models import Count
from django.utils.module_loading import import_string
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from tunables.api import problems
from tunables.api.actors import request_id, resolve_actor
from tunables.api.base import TunablesAPIView
from tunables.api.serializers import ChangeSetDetailSerializer, ChangesRequestSerializer, RollbackRequestSerializer
from tunables.changes import Change
from tunables.conf import settings
from tunables.errors import FieldWarning, GroupNotEditable
from tunables.models import ChangeSet
from tunables.services import apply_changeset, document_changes, rollback_changes, validate


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


def parsed_changes(request: Request) -> tuple[list[Change], str, bool]:
    serializer = ChangesRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    changes = [Change(item["key"], item.get("value"), item["reset"]) for item in data["changes"]]
    return changes, data["reason"], data["dry_run"]


def _check_editable(request: Request, changes: Sequence[Change]) -> None:
    hook = settings.EDITABLE_GROUPS
    if hook is None:
        return
    if isinstance(hook, str):
        hook = import_string(hook)
    editable = hook(request)
    if editable is None:
        return
    for change in changes:
        group = change.key.partition(".")[0]
        if group not in editable:
            raise GroupNotEditable(group)


def write(
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
    _check_editable(request, changes)
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


class Validate(TunablesAPIView):
    def post(self, request: Request) -> Response:
        changes, reason, _ = parsed_changes(request)
        return write(request, changes, source="api", reason=reason, dry_run=True)


class Rollback(TunablesAPIView):
    def post(self, request: Request) -> Response:
        serializer = RollbackRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        to_version = serializer.validated_data["to_version"]
        changes = rollback_changes(to_version)
        return write(
            request, changes, source="rollback", reason=serializer.validated_data["reason"], restores_version=to_version
        )


class Import(TunablesAPIView):
    def post(self, request: Request) -> Response:
        if not isinstance(request.data, Mapping):
            raise ValidationError("expected a snapshot document")
        strict = request.query_params.get("strict", "").lower() in ("1", "true", "yes")
        changes, warnings = document_changes(request.data, strict=strict)
        return write(request, changes, source="import", reason="", extra_warnings=warnings)
