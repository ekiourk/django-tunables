from typing import Any

from django.conf import settings as django_settings
from django.core.exceptions import ImproperlyConfigured

DEFAULTS: dict[str, Any] = {"ENVIRONMENT": ""}


class Settings:
    def __getattr__(self, name: str) -> Any:
        configured = getattr(django_settings, "TUNABLES", {})
        if name in configured:
            return configured[name]
        if name in DEFAULTS:
            return DEFAULTS[name]
        raise ImproperlyConfigured(f"TUNABLES[{name!r}] is not set")


settings = Settings()
