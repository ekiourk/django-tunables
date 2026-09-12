from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework.test import APIClient

from tests.catalogue import catalogue, pricing, thermostat
from tunables.models import State
from tunables.schema import describe_group

pytestmark = pytest.mark.django_db

BASE = "/api/tunables/"
CATALOGUE = "tests.catalogue.catalogue"

VAT_RATE = {
    "key": "pricing.vat_rate",
    "group": "pricing",
    "name": "vat_rate",
    "type": {"name": "float", "params": {"min": 0.0, "max": 1.0}},
    "default": 0.24,
    "title": "VAT rate",
    "description": "",
    "unit": "",
    "ui": {},
    "metadata": {},
    "deprecated": "",
}


def get(api: APIClient, path: str, **kwargs: Any) -> Any:
    response = api.get(BASE + path, **kwargs)
    assert response["X-Tunables-Version"] == "0"
    return response


def test_groups_list(api: APIClient) -> None:
    response = get(api, "groups/")
    assert response.status_code == 200
    assert response.json() == [
        {
            "name": "pricing",
            "title": "Pricing",
            "description": "Prices and shipping rules for the web shop.",
            "order": 1,
            "tunable_count": 4,
            "validators": [],
        },
        {
            "name": "thermostat",
            "title": "Thermostat",
            "description": "",
            "order": 2,
            "tunable_count": 5,
            "validators": [],
        },
        {
            "name": "weights",
            "title": "Weights",
            "description": "",
            "order": 3,
            "tunable_count": 3,
            "validators": ["The three weights must sum to 1."],
        },
    ]


def test_group_detail(api: APIClient) -> None:
    body = get(api, "groups/pricing/").json()
    assert body["definitions"][0] == VAT_RATE
    assert [d["name"] for d in body["definitions"]] == [t.name for t in pricing.tunables]
    assert {k: v for k, v in body.items() if k != "definitions"} == {
        "name": "pricing",
        "title": "Pricing",
        "description": "Prices and shipping rules for the web shop.",
        "order": 1,
        "ui": {},
        "metadata": {},
        "validators": [],
    }
    thermo = get(api, "groups/thermostat/").json()
    assert thermo["ui"] == thermostat.ui
    legacy = thermo["definitions"][-1]
    assert (legacy["key"], legacy["deprecated"]) == ("thermostat.legacy_offset", "Use target_c instead.")
    colour = thermo["definitions"][3]
    assert colour["type"] == {"name": "hex_colour", "params": {}}


def test_group_schema(api: APIClient) -> None:
    assert get(api, "groups/pricing/schema/").json() == describe_group(catalogue, pricing)
    body = get(api, "schema/").json()
    assert list(body) == ["pricing", "thermostat", "weights"]
    assert body["thermostat"] == describe_group(catalogue, thermostat)


def test_definitions(api: APIClient) -> None:
    body = get(api, "definitions/").json()
    assert [d["key"] for d in body] == list(catalogue.keys())
    assert body[0] == VAT_RATE


@pytest.mark.parametrize("path", ["groups/shop/", "groups/shop/schema/"])
def test_unknown_group_is_a_problem(api: APIClient, path: str) -> None:
    response = get(api, path)
    assert response.status_code == 404
    assert response["Content-Type"] == "application/problem+json"
    assert response.json() == {
        "type": "urn:tunables:problem:not-found",
        "title": "Not Found",
        "status": 404,
        "detail": "unknown group 'shop'",
    }


@pytest.mark.parametrize("path", ["groups/", "groups/pricing/", "schema/", "definitions/"])
def test_out_of_sync_is_503(api: APIClient, path: str) -> None:
    State.objects.update(catalogue_version="sha256:stale")
    response = get(api, path)
    assert response.status_code == 503
    assert response["Content-Type"] == "application/problem+json"
    body = response.json()
    assert (body["type"], body["title"], body["status"]) == (
        "urn:tunables:problem:catalogue-out-of-sync",
        "Service Unavailable",
        503,
    )
    assert "tunables_sync" in body["detail"]


def test_permission_classes_from_settings(api: APIClient) -> None:
    authenticated = {"CATALOGUE": CATALOGUE, "API_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"]}
    with override_settings(TUNABLES=authenticated):
        # DRF's default authenticators start with sessions, which issue no challenge, so anonymous is 403.
        response = get(api, "groups/")
        assert response.status_code == 403
        assert "WWW-Authenticate" not in response
        assert response.json()["type"] == "urn:tunables:problem:not-authenticated"
        api.force_authenticate(User.objects.create_user("alice"))
        assert get(api, "groups/").status_code == 200
    api.force_authenticate(None)
    basic = {**authenticated, "API_AUTHENTICATION_CLASSES": ["rest_framework.authentication.BasicAuthentication"]}
    with override_settings(TUNABLES=basic):
        response = get(api, "groups/")
        assert response.status_code == 401
        assert response["WWW-Authenticate"].startswith("Basic")
        assert response.json()["type"] == "urn:tunables:problem:not-authenticated"
    api.force_authenticate(None)
    admin_only = {"CATALOGUE": CATALOGUE, "API_PERMISSION_CLASSES": ["rest_framework.permissions.IsAdminUser"]}
    with override_settings(TUNABLES=admin_only):
        api.force_authenticate(User.objects.create_user("bob"))
        response = get(api, "groups/")
        assert response.status_code == 403
        assert response.json()["type"] == "urn:tunables:problem:permission-denied"


def test_authentication_classes_from_settings(api: APIClient) -> None:
    no_authenticators = {
        "CATALOGUE": CATALOGUE,
        "API_AUTHENTICATION_CLASSES": [],
        "API_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    }
    with override_settings(TUNABLES=no_authenticators):
        response = get(api, "groups/")
    assert response.status_code == 403
    assert "WWW-Authenticate" not in response
