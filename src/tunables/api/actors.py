from rest_framework.request import Request

from tunables.changes import Actor


def default_actor_resolver(request: Request) -> Actor:
    """Authenticated user -> verified; ACTOR_HEADER -> asserted; else anonymous, asserted."""
    raise NotImplementedError


def resolve_actor(request: Request) -> Actor:
    """Call the resolver named by TUNABLES["ACTOR_RESOLVER"]."""
    raise NotImplementedError


def request_id(request: Request) -> str:
    """Value of the TUNABLES["REQUEST_ID_HEADER"] header, or an empty string."""
    raise NotImplementedError
