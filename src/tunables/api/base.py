from collections.abc import Callable, Sequence
from typing import Any, ClassVar

from django.utils.module_loading import import_string
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import SAFE_METHODS
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from tunables.api import problems
from tunables.conf import settings
from tunables.errors import CatalogueOutOfSync
from tunables.models import State
from tunables.sync import is_synced


def _instantiate(entries: Sequence[Any]) -> list[Any]:
    return [(import_string(entry) if isinstance(entry, str) else entry)() for entry in entries]


class TunablesAPIView(APIView):
    """Shared behaviour: settings-driven auth, sync check, X-Tunables-Version header, problem responses."""

    reads_need_sync: ClassVar[bool] = True

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
        if request.method in SAFE_METHODS and not self.reads_need_sync:
            return
        if not is_synced():
            raise CatalogueOutOfSync("catalogue changed since the last sync; run tunables_sync")

    def finalize_response(self, request: Request, response: Response, *args: Any, **kwargs: Any) -> Response:
        response = super().finalize_response(request, response, *args, **kwargs)
        version = State.objects.filter(pk=1).values_list("current_version", flat=True).first()
        if version is not None:
            response["X-Tunables-Version"] = str(version)
        return response
