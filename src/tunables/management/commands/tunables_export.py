import json
from datetime import datetime
from pathlib import Path
from typing import Any

from django.core.management.base import CommandError, CommandParser

from tunables.document import defaults_document
from tunables.management.base import TunablesCommand
from tunables.registry import get_catalogue
from tunables.schema import document_schema
from tunables.services import latest_snapshot


class Command(TunablesCommand):
    help = "Write the latest snapshot document as JSON."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--output", help="File to write. Defaults to standard output.")
        parser.add_argument(
            "--defaults",
            action="store_true",
            help="Write the version-zero document from the catalogue in code instead of the latest snapshot.",
        )
        parser.add_argument(
            "--created-at",
            help="With --defaults: stamp this ISO 8601 datetime with an offset instead of now, for reproducible files.",
        )
        parser.add_argument(
            "--schema",
            action="store_true",
            help="Write the JSON Schema of snapshot documents for the catalogue in code instead of a document.",
        )

    def handle(self, *_: Any, **options: Any) -> None:
        if options["defaults"] and options["schema"]:
            raise CommandError("--defaults and --schema cannot be combined")
        created_at = None
        if options["created_at"] is not None:
            if not options["defaults"]:
                raise CommandError("--created-at needs --defaults")
            created_at = _parse_created_at(options["created_at"])
        if options["schema"]:
            payload = document_schema(get_catalogue())
        elif options["defaults"]:
            payload = defaults_document(get_catalogue(), created_at=created_at)
        else:
            payload = latest_snapshot().document
        text = json.dumps(payload, indent=2) + "\n"
        if options["output"] is None:
            self.stdout.write(text, ending="")
            return
        Path(options["output"]).write_text(text)
        self.stdout.write(f"wrote {options['output']}")


def _parse_created_at(raw: str) -> datetime:
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as error:
        raise CommandError(f"--created-at must be an ISO 8601 datetime, got {raw!r}") from error
    if value.tzinfo is None:
        raise CommandError("--created-at must carry a UTC offset, for example 2026-09-01T00:00:00+00:00")
    return value
