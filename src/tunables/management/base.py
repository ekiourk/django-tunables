from typing import Any

from django.core.management.base import BaseCommand, CommandError

from tunables.errors import CatalogueValidationError, FieldError, GroupError, TunablesError, ValidationFailed


class TunablesCommand(BaseCommand):
    """BaseCommand that reports tunables errors as command errors."""

    def execute(self, *args: Any, **options: Any) -> Any:
        try:
            return super().execute(*args, **options)
        except ValidationFailed as error:
            raise CommandError("\n".join(describe(item) for item in error.errors)) from error
        except TunablesError as error:
            raise CommandError(str(error)) from error


def describe(error: FieldError | GroupError | CatalogueValidationError) -> str:
    if isinstance(error, CatalogueValidationError):
        return f"catalogue: {error.code}: {error.message}"
    subject = error.key if isinstance(error, FieldError) else f"group {error.group}"
    return f"{subject}: {error.code}: {error.message}"
