from django.apps import apps

from tunables.apps import TunablesConfig


def test_app_is_installed() -> None:
    config = apps.get_app_config("tunables")
    assert isinstance(config, TunablesConfig)
    assert config.label == "tunables"
