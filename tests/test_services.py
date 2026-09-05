import pytest

from tests.catalogue import catalogue
from tests.test_sync import override
from tunables import services
from tunables.errors import CatalogueOutOfSync
from tunables.sync import SyncResult

pytestmark = pytest.mark.django_db


def test_current_version_requires_sync() -> None:
    with pytest.raises(CatalogueOutOfSync):
        services.current_version()


def test_current_version_after_sync(synced: SyncResult) -> None:
    assert services.current_version() == 0
    override("pricing.vat_rate", 0.2)
    assert services.current_version() == 1


def test_current_values_are_defaults_without_overrides(synced: SyncResult) -> None:
    assert services.current_values() == catalogue.defaults()


def test_current_values_overlay_coerced_overrides(synced: SyncResult) -> None:
    override("pricing.vat_rate", 0.2)
    override("thermostat.display_colour", "#ABCDEF")
    values = services.current_values()
    assert values["pricing"]["vat_rate"] == 0.2
    assert values["thermostat"]["display_colour"] == "#abcdef"
    assert values["pricing"]["free_shipping_over"] == 50.0
