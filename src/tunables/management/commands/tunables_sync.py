from typing import Any

from django.core.management.base import CommandError, CommandParser

from tunables.management.base import TunablesCommand
from tunables.services import current_version
from tunables.sync import is_synced, sync


class Command(TunablesCommand):
    help = "Mirror the catalogue into the database and create the initial state and snapshot."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--check",
            action="store_true",
            help="Exit 1 when the stored catalogue version differs from the code. Writes nothing.",
        )

    def handle(self, *_: Any, **options: Any) -> None:
        if options["check"]:
            if not is_synced():
                raise CommandError("out of sync; run tunables_sync", returncode=1)
            self.stdout.write(f"in sync at version {current_version()}")
            return
        result = sync()
        if result.created:
            self.stdout.write("created state and snapshot 0")
        elif result.rebuilt:
            self.stdout.write(f"catalogue changed, wrote version {result.version}")
        else:
            self.stdout.write(f"in sync at version {result.version}")
