from django.apps import apps

from tunables.apps import TunablesConfig


def test_app_is_installed() -> None:
    config = apps.get_app_config("tunables")
    assert isinstance(config, TunablesConfig)
    assert config.label == "tunables"


def test_the_receivers_are_connected_by_the_app_not_by_import() -> None:
    import inspect

    from django.core.signals import setting_changed

    from tunables import publishers, reader, registry
    from tunables.apps import TunablesConfig
    from tunables.signals import snapshot_published

    for module in (reader, registry, publishers):
        assert "@receiver(" not in inspect.getsource(module), module.__name__
    assert "ready" in vars(TunablesConfig)
    connected = {r[1]() for r in setting_changed.receivers} | {r[1]() for r in snapshot_published.receivers}
    names = {getattr(r, "__name__", "") for r in connected if r is not None}
    assert {"_invalidate_on_publish", "_invalidate_on_setting_changed"} <= names
