"""Prepare the demo database: migrate, sync the catalogue and create the users. Safe to run again."""

from typing import Any

from django.contrib.auth.models import Permission, User
from django.core.management import call_command
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
    help = "Migrate, sync the catalogue and create the demo users (password 'demo')."

    def handle(self, *_: Any, **__: Any) -> None:
        call_command("migrate", verbosity=0)
        call_command("tunables_sync")
        for username, (superuser, codenames) in USERS.items():
            if User.objects.filter(username=username).exists():
                self.stdout.write(f"{username}: exists")
                continue
            user = User.objects.create_user(username, password=PASSWORD, is_staff=superuser, is_superuser=superuser)
            user.user_permissions.set(
                Permission.objects.filter(content_type__app_label="tunables", codename__in=codenames)
            )
            self.stdout.write(f"{username}: created")
