from django.apps import AppConfig


class TunablesConfig(AppConfig):
    name = "tunables"
    label = "tunables"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from django.core.signals import setting_changed

        from tunables import publishers, reader, registry
        from tunables.signals import snapshot_published

        snapshot_published.connect(reader._invalidate_on_publish)
        setting_changed.connect(reader._invalidate_on_setting_changed)
        setting_changed.connect(registry._on_setting_changed)
        setting_changed.connect(publishers._on_setting_changed)
