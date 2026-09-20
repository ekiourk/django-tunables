from datetime import datetime
from typing import Any

from django.core.management.base import CommandError, CommandParser
from django.utils.dateparse import parse_datetime

from tunables.management.base import TunablesCommand
from tunables.services import prune_snapshots


class Command(TunablesCommand):
    help = "Remove snapshot documents outside a retention policy. Change sets and items are never touched."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--keep", type=int, help="Keep the newest N snapshots.")
        parser.add_argument("--before", help="Keep snapshots created at or after this ISO 8601 instant.")
        parser.add_argument("--dry-run", action="store_true", help="Report what would go without deleting.")
        parser.add_argument("--batch-size", type=int, default=500, help="Versions deleted per statement.")

    def handle(self, *_: Any, **options: Any) -> None:
        if (options["keep"] is None) == (options["before"] is None):
            raise CommandError("pass --keep or --before, not both and not neither")
        if options["batch_size"] < 1:
            raise CommandError("--batch-size must be at least 1")
        before = _instant(options["before"]) if options["before"] else None
        removed = prune_snapshots(
            keep=options["keep"], before=before, dry_run=options["dry_run"], batch_size=options["batch_size"]
        )
        if not removed:
            self.stdout.write("no snapshots to remove")
            return
        verb = "would remove" if options["dry_run"] else "removed"
        if len(removed) == 1:
            self.stdout.write(f"{verb} 1 snapshot: version {removed[0]}")
        else:
            self.stdout.write(f"{verb} {len(removed)} snapshots: versions {removed[0]} to {removed[-1]}")


def _instant(raw: str) -> datetime:
    try:
        parsed = parse_datetime(raw)
    except ValueError as error:
        raise CommandError(f"--before must be an ISO 8601 datetime, got {raw!r}") from error
    if parsed is None:
        raise CommandError(f"--before must be an ISO 8601 datetime, got {raw!r}")
    if parsed.tzinfo is None:
        raise CommandError("--before must carry a UTC offset, for example 2026-06-01T00:00:00+00:00")
    return parsed
