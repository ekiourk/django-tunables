from http import HTTPStatus
from typing import Any

from rest_framework.exceptions import APIException
from rest_framework.response import Response

from tunables.errors import (
    CatalogueOutOfSync,
    CatalogueValidationError,
    FieldError,
    FieldWarning,
    GroupError,
    GroupNotEditable,
    NothingToChange,
    TagExists,
    TagNotAllowed,
    TagSeeded,
    UnknownTag,
    UnknownVersion,
    ValidationFailed,
    VersionConflict,
)

CONTENT_TYPE = "application/problem+json"


def problem(status: int, slug: str, title: str, detail: str = "", **extra: Any) -> Response:
    """RFC 9457 problem response with type urn:tunables:problem:<slug>."""
    body: dict[str, Any] = {"type": f"urn:tunables:problem:{slug}", "title": title, "status": status}
    if detail:
        body["detail"] = detail
    body.update(extra)
    return Response(body, status=status, content_type=CONTENT_TYPE)


def exception_handler(exc: Exception, _context: dict[str, Any]) -> Response | None:
    """DRF exception handler mapping tunables and DRF errors to problem responses."""
    if isinstance(exc, CatalogueOutOfSync):
        return problem(503, "catalogue-out-of-sync", HTTPStatus.SERVICE_UNAVAILABLE.phrase, str(exc))
    if isinstance(exc, ValidationFailed):
        errors = [describe(error) for error in exc.errors]
        return problem(422, "validation-failed", "Validation failed", str(exc), errors=errors)
    if isinstance(exc, VersionConflict):
        versions = {"expected_version": exc.expected, "current_version": exc.actual}
        return problem(412, "version-conflict", "Version conflict", str(exc), **versions)
    if isinstance(exc, NothingToChange):
        return problem(400, "nothing-to-change", "Nothing to change", str(exc))
    if isinstance(exc, UnknownVersion):
        return problem(422, "unknown-version", "Unknown version", str(exc), version=exc.version)
    if isinstance(exc, UnknownTag):
        return problem(422, "unknown-tag", "Unknown tag", str(exc), tag=exc.name)
    if isinstance(exc, GroupNotEditable):
        return problem(403, "forbidden-group", "Forbidden group", str(exc), group=exc.group)
    if isinstance(exc, TagExists):
        return problem(409, "tag-exists", "Tag exists", str(exc), name=exc.name)
    if isinstance(exc, TagNotAllowed):
        return problem(403, "forbidden-tag", "Forbidden tag", str(exc), name=exc.name)
    if isinstance(exc, TagSeeded):
        return problem(409, "tag-seeded", "Tag seeded", str(exc), name=exc.name)
    if isinstance(exc, APIException):
        codes = exc.get_codes()
        slug = str(codes if isinstance(codes, str) else exc.default_code).replace("_", "-")
        detail = str(exc.detail) if isinstance(exc.detail, str) else ""
        extra: dict[str, Any] = {} if detail else {"errors": exc.detail}
        response = problem(exc.status_code, slug, HTTPStatus(exc.status_code).phrase, detail, **extra)
        auth_header = getattr(exc, "auth_header", None)
        if auth_header:
            response["WWW-Authenticate"] = auth_header
        return response
    return None


def describe(item: FieldError | GroupError | FieldWarning | CatalogueValidationError) -> dict[str, Any]:
    if isinstance(item, CatalogueValidationError):
        return {"scope": "catalogue", "code": item.code, "detail": item.message}
    subject = {"group": item.group} if isinstance(item, GroupError) else {"key": item.key}
    return {**subject, "code": item.code, "detail": item.message}
