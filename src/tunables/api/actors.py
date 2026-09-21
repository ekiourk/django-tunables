from django.utils.module_loading import import_string
from rest_framework.request import Request

from tunables.changes import FIELD_WIDTH, Actor
from tunables.conf import settings


def default_actor_resolver(request: Request) -> Actor:
    """Authenticated user -> verified; ACTOR_HEADER -> asserted; else anonymous, asserted."""
    client = request.headers.get(settings.CLIENT_HEADER, "")
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return Actor(user.get_username(), "verified", client=client)
    asserted = request.headers.get(settings.ACTOR_HEADER, "")
    return Actor(asserted or "anonymous", "asserted", client=client)


def resolve_actor(request: Request) -> Actor:
    """Call the resolver named by TUNABLES["ACTOR_RESOLVER"]."""
    resolver = settings.ACTOR_RESOLVER
    if isinstance(resolver, str):
        resolver = import_string(resolver)
    actor: Actor = resolver(request)
    return actor


def request_id(request: Request) -> str:
    """Value of the TUNABLES["REQUEST_ID_HEADER"] header, or an empty string. Cut to the column width."""
    return str(request.headers.get(settings.REQUEST_ID_HEADER, ""))[:FIELD_WIDTH]
