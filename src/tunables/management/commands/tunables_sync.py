from collections.abc import Sequence
from typing import Any

from django.core.management.base import CommandError, CommandParser

from tunables.errors import CatalogueValidationError, GroupError, format_error
from tunables.management.base import TunablesCommand
from tunables.services import current_version, rule_violations
from tunables.sync import is_synced, mirror_drift, sync


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
            drift = mirror_drift()
            if drift:
                raise CommandError("mirror out of date; run tunables_sync:\n" + "\n".join(drift), returncode=1)
            self.stdout.write(f"in sync at version {current_version()}")
            self._warn(rule_violations())
            return
        result = sync()
        if result.created:
            self.stdout.write("created state and snapshot 0")
        elif result.rebuilt:
            self.stdout.write(f"catalogue changed, wrote version {result.version}")
        else:
            self.stdout.write(f"in sync at version {result.version}")
        self._warn(result.violations)

    def _warn(self, violations: Sequence[GroupError | CatalogueValidationError]) -> None:
        for violation in violations:
            self.stderr.write(f"warning: {format_error(violation)}")
