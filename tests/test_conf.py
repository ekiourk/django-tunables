from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from tunables import conf


def test_reads_django_setting() -> None:
    assert conf.settings.CATALOGUE == "tests.catalogue.catalogue"


def test_follows_override_settings() -> None:
    with override_settings(TUNABLES={"CATALOGUE": "other.path"}):
        assert conf.settings.CATALOGUE == "other.path"
    assert conf.settings.CATALOGUE == "tests.catalogue.catalogue"


def test_missing_key_without_default_raises() -> None:
    with override_settings(TUNABLES={}), pytest.raises(ImproperlyConfigured, match="CATALOGUE"):
        _ = conf.settings.CATALOGUE


def test_missing_tunables_setting_raises(settings: Any) -> None:
    del settings.TUNABLES
    with pytest.raises(ImproperlyConfigured, match="CATALOGUE"):
        _ = conf.settings.CATALOGUE
