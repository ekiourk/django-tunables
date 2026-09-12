from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView


class TunablesAPIView(APIView):
    """Shared behaviour: settings-driven auth, sync check, X-Tunables-Version header, problem responses."""


class GroupList(TunablesAPIView):
    def get(self, request: Request) -> Response:
        raise NotImplementedError


class GroupDetail(TunablesAPIView):
    def get(self, request: Request, group: str) -> Response:
        raise NotImplementedError


class GroupSchema(TunablesAPIView):
    def get(self, request: Request, group: str) -> Response:
        raise NotImplementedError


class SchemaList(TunablesAPIView):
    def get(self, request: Request) -> Response:
        raise NotImplementedError


class DefinitionList(TunablesAPIView):
    def get(self, request: Request) -> Response:
        raise NotImplementedError


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
