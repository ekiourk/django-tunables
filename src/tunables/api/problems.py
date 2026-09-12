from http import HTTPStatus
from typing import Any

from rest_framework.exceptions import APIException
from rest_framework.response import Response

from tunables.errors import CatalogueOutOfSync

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
    if isinstance(exc, APIException):
        codes = exc.get_codes()
        slug = str(codes if isinstance(codes, str) else exc.default_code).replace("_", "-")
        detail = str(exc.detail) if isinstance(exc.detail, str) else ""
        extra = {} if detail else {"errors": exc.detail}
        response = problem(exc.status_code, slug, HTTPStatus(exc.status_code).phrase, detail, **extra)
        auth_header = getattr(exc, "auth_header", None)
        if auth_header:
            response["WWW-Authenticate"] = auth_header
        return response
    return None
