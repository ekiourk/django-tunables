from typing import Any

from django.core.management.base import CommandParser
from django.db import DEFAULT_DB_ALIAS

from tunables.management.base import TunablesCommand


class Command(TunablesCommand):
    help = (
        "Install PostgreSQL triggers that reject UPDATE, DELETE and TRUNCATE on the history tables. "
        "Needs PostgreSQL 14 or newer."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--remove", action="store_true", help="Drop the triggers instead.")
        parser.add_argument("--database", default=DEFAULT_DB_ALIAS, help="Database alias. Defaults to 'default'.")

    def handle(self, *_: Any, **options: Any) -> None:
        raise NotImplementedError
