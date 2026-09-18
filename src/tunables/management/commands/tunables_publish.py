from typing import Any

from django.core.management.base import CommandError, CommandParser

from tunables.management.base import TunablesCommand
from tunables.publishers import republish


class Command(TunablesCommand):
    help = "Send a stored snapshot, the latest by default, to the configured publishers again."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("version", nargs="?", type=int, help="Snapshot version to publish. Defaults to the latest.")
        parser.add_argument("--publisher", help="Dotted path of one configured publisher. Defaults to all.")

    def handle(self, *_: Any, **options: Any) -> None:
        try:
            results = republish(version=options["version"], publisher=options["publisher"])
        except ValueError as error:
            raise CommandError(str(error)) from error
        failures = []
        for result in results:
            if result.error:
                failures.append(f"{result.publisher}: {result.error}")
            else:
                self.stdout.write(f"{result.publisher}: published version {result.version}")
        if failures:
            raise CommandError("\n".join(failures))
