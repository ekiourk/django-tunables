from typing import Any

from django.core.management.base import CommandError, CommandParser
from django.db import DEFAULT_DB_ALIAS, connections

from tunables.management.base import TunablesCommand
from tunables.models import ChangeItem, ChangeSet, Snapshot

FUNCTION = "tunables_history_append_only"
TABLES = [model._meta.db_table for model in (ChangeSet, ChangeItem, Snapshot)]
# A snapshot is a materialisation of a version, rebuildable from the change items, so retention may
# delete one. The change sets and items are the audit trail and stay undeletable.
DELETABLE = [Snapshot._meta.db_table]

CREATE_FUNCTION = f"""
CREATE OR REPLACE FUNCTION {FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'tunables history is append-only: % on %', TG_OP, TG_TABLE_NAME;
END
$$
"""


class Command(TunablesCommand):
    help = (
        "Install PostgreSQL triggers that reject UPDATE and TRUNCATE on the history tables, and DELETE on "
        "every table but the snapshots, which retention may prune. Needs PostgreSQL 14 or newer."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--remove", action="store_true", help="Drop the triggers instead.")
        parser.add_argument("--database", default=DEFAULT_DB_ALIAS, help="Database alias. Defaults to 'default'.")

    def handle(self, *_: Any, **options: Any) -> None:
        connection = connections[options["database"]]
        if connection.vendor != "postgresql":
            raise CommandError(
                f"tunables_protect_history needs PostgreSQL; database {options['database']!r} is {connection.vendor}"
            )
        with connection.cursor() as cursor:
            if options["remove"]:
                for table in TABLES:
                    cursor.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
                    cursor.execute(f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table}")
                cursor.execute(f"DROP FUNCTION IF EXISTS {FUNCTION}()")
                self.stdout.write("removed history protection")
                return
            cursor.execute(CREATE_FUNCTION)
            for table in TABLES:
                events = "UPDATE" if table in DELETABLE else "UPDATE OR DELETE"
                cursor.execute(
                    f"CREATE OR REPLACE TRIGGER {table}_append_only BEFORE {events} ON {table} "
                    f"FOR EACH ROW EXECUTE FUNCTION {FUNCTION}()"
                )
                cursor.execute(
                    f"CREATE OR REPLACE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
                    f"FOR EACH STATEMENT EXECUTE FUNCTION {FUNCTION}()"
                )
            self.stdout.write("installed history protection")
