from unittest import mock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from tests.catalogue import catalogue, pricing
from tunables import Catalogue, registry

other = Catalogue([pricing], label="other")
calls = 0


def build() -> Catalogue:
    global calls
    calls += 1
    return other


@pytest.fixture(autouse=True)
def fresh_registry() -> None:
    registry.reset()
    global calls
    calls = 0


def test_loads_instance_from_dotted_path() -> None:
    assert registry.get_catalogue() is catalogue


def test_loads_from_callable_and_calls_it_once() -> None:
    with override_settings(TUNABLES={"CATALOGUE": f"{__name__}.build"}):
        assert registry.get_catalogue() is other
        assert registry.get_catalogue() is other
    assert calls == 1


def test_caches_instance() -> None:
    with mock.patch("tunables.registry.import_string", wraps=registry.import_string) as spy:
        registry.get_catalogue()
        registry.get_catalogue()
    assert spy.call_count == 1


def test_reset_forces_reload() -> None:
    with mock.patch("tunables.registry.import_string", wraps=registry.import_string) as spy:
        registry.get_catalogue()
        registry.reset()
        registry.get_catalogue()
    assert spy.call_count == 2


def test_override_settings_resets_without_explicit_reset() -> None:
    assert registry.get_catalogue() is catalogue
    with override_settings(TUNABLES={"CATALOGUE": f"{__name__}.other"}):
        assert registry.get_catalogue() is other
    assert registry.get_catalogue() is catalogue


def test_not_a_catalogue_raises() -> None:
    with (
        override_settings(TUNABLES={"CATALOGUE": f"{__name__}.calls"}),
        pytest.raises(ImproperlyConfigured, match="Catalogue"),
    ):
        registry.get_catalogue()


def test_missing_setting_raises() -> None:
    with override_settings(TUNABLES={}), pytest.raises(ImproperlyConfigured, match="CATALOGUE"):
        registry.get_catalogue()
