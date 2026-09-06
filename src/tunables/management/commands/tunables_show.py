from typing import Any

from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    help = "Print the effective values of the latest snapshot, marking overrides."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--group", help="Only this group.")
        parser.add_argument("--json", action="store_true", help="Print the snapshot document as JSON.")

    def handle(self, *args: Any, **options: Any) -> None:
        raise NotImplementedError
