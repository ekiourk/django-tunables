from typing import Any

from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    help = "Apply the values of a snapshot document as one change set."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("file", help="Snapshot document to import.")
        parser.add_argument("--actor", required=True, help="Recorded as the actor of the change set.")
        parser.add_argument("--reason", default="", help="Recorded as the reason of the change set.")
        parser.add_argument("--strict", action="store_true", help="Fail on keys not in the catalogue.")

    def handle(self, *args: Any, **options: Any) -> None:
        raise NotImplementedError
