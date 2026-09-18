import json
from pathlib import Path
from typing import Any

from django.core.management.base import CommandParser

from tunables.document import defaults_document
from tunables.management.base import TunablesCommand
from tunables.registry import get_catalogue
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

    def handle(self, *_: Any, **options: Any) -> None:
        document = defaults_document(get_catalogue()) if options["defaults"] else latest_snapshot().document
        text = json.dumps(document, indent=2) + "\n"
        if options["output"] is None:
            self.stdout.write(text, ending="")
            return
        Path(options["output"]).write_text(text)
        self.stdout.write(f"wrote {options['output']}")
