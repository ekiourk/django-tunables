from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.dispatch import receiver
from django.utils.module_loading import import_string

from tunables.catalogue import Catalogue
from tunables.conf import settings

_catalogue: Catalogue | None = None


def get_catalogue() -> Catalogue:
    global _catalogue
    if _catalogue is None:
        target = import_string(settings.CATALOGUE)
        if not isinstance(target, Catalogue) and callable(target):
            target = target()
        if not isinstance(target, Catalogue):
            raise ImproperlyConfigured(f"TUNABLES['CATALOGUE'] must point at a Catalogue, got {type(target).__name__}")
        _catalogue = target
    return _catalogue


def reset() -> None:
    global _catalogue
    _catalogue = None


@receiver(setting_changed)
def _on_setting_changed(*, setting: str, **_: Any) -> None:
    if setting == "TUNABLES":
        reset()
