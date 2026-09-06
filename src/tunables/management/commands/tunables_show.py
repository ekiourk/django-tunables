import json
from typing import Any

from django.core.management.base import CommandError, CommandParser

from tunables.management.base import TunablesCommand
from tunables.services import latest_snapshot


class Command(TunablesCommand):
    help = "Print the effective values of the latest snapshot, marking overrides."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--group", help="Only this group.")
        parser.add_argument("--json", action="store_true", help="Print the snapshot document as JSON.")

    def handle(self, *_: Any, **options: Any) -> None:
        document = latest_snapshot().document
        if options["json"]:
            self.stdout.write(json.dumps(document, indent=2))
            return
        groups = document["groups"]
        if options["group"] is not None:
            if options["group"] not in groups:
                raise CommandError(f"unknown group {options['group']!r}")
            groups = {options["group"]: groups[options["group"]]}
        overridden = set(document["overridden"])
        self.stdout.write(f"version {document['version']}")
        for group, values in groups.items():
            for name, value in values.items():
                key = f"{group}.{name}"
                marker = "  (override)" if key in overridden else ""
                self.stdout.write(f"{key} = {json.dumps(value)}{marker}")
