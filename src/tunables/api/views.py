from collections.abc import Callable, Sequence
from typing import Any

from django.utils.module_loading import import_string
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from tunables.api import problems
from tunables.catalogue import Catalogue, Group, Tunable
from tunables.conf import settings
from tunables.errors import CatalogueOutOfSync
from tunables.models import State
from tunables.registry import get_catalogue
from tunables.schema import describe_group, validator_description
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
        "tunable_count": len(group.tunables),
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
        return Response([_summary(group) for group in get_catalogue().groups.values()])


class GroupDetail(TunablesAPIView):
    def get(self, request: Request, group: str) -> Response:
        found = _group(get_catalogue(), group)
        summary = _summary(found)
        del summary["tunable_count"]
        return Response(
            {
                **summary,
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


class Values(TunablesAPIView):
    def get(self, request: Request) -> Response:
        raise NotImplementedError


class GroupValues(TunablesAPIView):
    def get(self, request: Request, group: str) -> Response:
        raise NotImplementedError


class ChangeSetList(TunablesAPIView):
    def get(self, request: Request) -> Response:
        raise NotImplementedError


class ChangeSetDetail(TunablesAPIView):
    def get(self, request: Request, version: int) -> Response:
        raise NotImplementedError


class LatestSnapshot(TunablesAPIView):
    def get(self, request: Request) -> Response:
        raise NotImplementedError


class SnapshotDetail(TunablesAPIView):
    def get(self, request: Request, version: int) -> Response:
        raise NotImplementedError


class Export(TunablesAPIView):
    def get(self, request: Request) -> Response:
        raise NotImplementedError
