from typing import Any

from django.conf import settings as django_settings
from django.core.exceptions import ImproperlyConfigured

DEFAULTS: dict[str, Any] = {
    "ENVIRONMENT": "",
    "PUBLISHERS": [],
    "FILE_PUBLISHER_PATH": None,
    "PAGE_SIZE": 50,
    "API_AUTHENTICATION_CLASSES": None,
    "API_PERMISSION_CLASSES": None,
    "ACTOR_RESOLVER": "tunables.api.actors.default_actor_resolver",
    "EDITABLE_GROUPS": None,
    "ACTOR_HEADER": "X-Tunables-Actor",
    "CLIENT_HEADER": "X-Tunables-Client",
    "REQUEST_ID_HEADER": "X-Request-ID",
    "REASON_HEADER": "X-Tunables-Reason",
    "READ_CACHE_TTL": 1.0,
}


class Settings:
    def __getattr__(self, name: str) -> Any:
        configured = getattr(django_settings, "TUNABLES", {})
        if name in configured:
            return configured[name]
        if name in DEFAULTS:
            return DEFAULTS[name]
        raise ImproperlyConfigured(f"TUNABLES[{name!r}] is not set")


settings = Settings()
