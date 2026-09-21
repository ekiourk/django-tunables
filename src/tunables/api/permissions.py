"""A DRF permission class that answers with the Django permissions the admin checks."""

from typing import Any

from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.request import Request

TAG_PERMISSIONS = {"POST": "add_tag", "PUT": "change_tag", "PATCH": "change_tag", "DELETE": "delete_tag"}


class TunablesPermissions(BasePermission):
    """Reads need view_tunabledefinition, tag writes the Tag permissions, other writes add_changeset."""

    def has_permission(self, request: Request, view: Any) -> bool:
        user = getattr(request, "user", None)
        return user is not None and user.has_perm(self._required(request, view))

    def _required(self, request: Request, view: Any) -> str:
        """The permission this request needs, as 'tunables.<codename>'."""
        if request.method in SAFE_METHODS:
            return "tunables.view_tunabledefinition"
        if getattr(view, "permission_scope", "value") == "tag":
            return f"tunables.{TAG_PERMISSIONS.get(str(request.method), 'change_tag')}"
        return "tunables.add_changeset"


def may_create_tags(request: Request, view: Any) -> bool:
    """True unless this deployment aligned the API with Django permissions and the caller lacks add_tag."""
    if not any(isinstance(permission, TunablesPermissions) for permission in view.get_permissions()):
        return True
    user = getattr(request, "user", None)
    return bool(user is not None and user.has_perm("tunables.add_tag"))
