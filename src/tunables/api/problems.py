from typing import Any

from rest_framework.response import Response

CONTENT_TYPE = "application/problem+json"


def problem(status: int, slug: str, title: str, detail: str = "", **extra: Any) -> Response:
    """RFC 9457 problem response with type urn:tunables:problem:<slug>."""
    raise NotImplementedError


def exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """DRF exception handler mapping tunables and DRF errors to problem responses."""
    raise NotImplementedError
