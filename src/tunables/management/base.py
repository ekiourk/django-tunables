from typing import Any

from django.core.management.base import BaseCommand, CommandError

from tunables.errors import TunablesError, ValidationFailed, format_error


class TunablesCommand(BaseCommand):
    """BaseCommand that reports tunables errors as command errors."""

    def execute(self, *args: Any, **options: Any) -> Any:
        try:
            return super().execute(*args, **options)
        except ValidationFailed as error:
            raise CommandError("\n".join(format_error(item) for item in error.errors)) from error
        except TunablesError as error:
            raise CommandError(str(error)) from error
