import json
from pathlib import Path
from typing import Any

from django.core.management.base import CommandError, CommandParser

from tunables.changes import Actor
from tunables.errors import NothingToChange
from tunables.management.base import TunablesCommand
from tunables.services import apply_changeset, document_changes


class Command(TunablesCommand):
    help = "Apply the values of a snapshot document as one change set."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("file", help="Snapshot document to import.")
        parser.add_argument("--actor", required=True, help="Recorded as the actor of the change set.")
        parser.add_argument("--reason", default="", help="Recorded as the reason of the change set.")
        parser.add_argument("--strict", action="store_true", help="Fail on keys not in the catalogue.")
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Treat the document as the complete state: reset every override it does not name.",
        )

    def handle(self, *_: Any, **options: Any) -> None:
        try:
            document = json.loads(Path(options["file"]).read_text())
        except json.JSONDecodeError as error:
            raise CommandError(f"{options['file']} is not valid JSON: {error}") from error
        changes, warnings = document_changes(document, strict=options["strict"], replace=options["replace"])
        for warning in warnings:
            self.stderr.write(f"skipped {warning.key}: {warning.message}")
        try:
            result = apply_changeset(
                changes, actor=Actor(options["actor"], "system"), source="import", reason=options["reason"]
            )
        except NothingToChange:
            self.stdout.write("nothing to change")
            return
        self.stdout.write(f"wrote version {result.version}")
