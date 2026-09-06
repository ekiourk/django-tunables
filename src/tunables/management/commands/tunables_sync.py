from typing import Any

from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    help = "Mirror the catalogue into the database and create the initial state and snapshot."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--check",
            action="store_true",
            help="Exit 1 when the stored catalogue version differs from the code. Writes nothing.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        raise NotImplementedError
