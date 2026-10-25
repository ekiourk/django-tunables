"""Create the demo users. Safe to run again: existing users are left alone."""

from typing import Any

from django.contrib.auth.models import Permission, User
from django.core.management.base import BaseCommand

PASSWORD = "demo"

# username -> (is_superuser, permission codenames)
USERS: dict[str, tuple[bool, tuple[str, ...]]] = {
    "admin": (True, ()),
    "writer": (False, ("add_changeset", "view_tunabledefinition")),
    "viewer": (False, ("view_tunabledefinition",)),
    "nobody": (False, ()),
}


class Command(BaseCommand):
    help = "Create the demo users admin, writer, viewer and nobody, all with the password 'demo'."

    def handle(self, *_: Any, **__: Any) -> None:
        for username, (superuser, codenames) in USERS.items():
            if User.objects.filter(username=username).exists():
                self.stdout.write(f"{username}: exists")
                continue
            if superuser:
                user = User.objects.create_superuser(username, password=PASSWORD)
            else:
                user = User.objects.create_user(username, password=PASSWORD)
                granted = Permission.objects.filter(content_type__app_label="tunables", codename__in=codenames)
                user.user_permissions.set(granted)
            self.stdout.write(f"{username}: created")
