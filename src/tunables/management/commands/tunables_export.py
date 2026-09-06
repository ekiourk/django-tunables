import json
from pathlib import Path
from typing import Any

from django.core.management.base import CommandParser

from tunables.management.base import TunablesCommand
from tunables.services import latest_snapshot


class Command(TunablesCommand):
    help = "Write the latest snapshot document as JSON."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--output", help="File to write. Defaults to standard output.")

    def handle(self, *_: Any, **options: Any) -> None:
        text = json.dumps(latest_snapshot().document, indent=2) + "\n"
        if options["output"] is None:
            self.stdout.write(text, ending="")
            return
        Path(options["output"]).write_text(text)
        self.stdout.write(f"wrote {options['output']}")
