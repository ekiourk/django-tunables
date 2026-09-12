from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework.test import APIClient

from tests.test_services import apply
from tunables import Actor, Change
from tunables.models import ChangeSet, State, TunableValue

pytestmark = pytest.mark.django_db

BASE = "/api/tunables/"
CATALOGUE = "tests.catalogue.catalogue"
PROBLEM = "application/problem+json"


def settings_with(**extra: Any) -> Any:
    return override_settings(TUNABLES={"CATALOGUE": CATALOGUE, **extra})


def post(api: APIClient, path: str, body: Any, **headers: str) -> Any:
    return api.post(BASE + path, body, format="json", **headers)


def changes(*items: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"changes": list(items), **extra}


def counts() -> tuple[int, int]:
    return ChangeSet.objects.count(), TunableValue.objects.count()


def test_post_changesets_success(api: APIClient) -> None:
    body = changes(
        {"key": "pricing.vat_rate", "value": 0.2}, {"key": "thermostat.mode", "value": "heat"}, reason="winter"
    )
    response = post(api, "changesets/", body)
    assert response.status_code == 201
    assert response["X-Tunables-Version"] == "1"
    data = response.json()
    assert set(data) == {"version", "changeset", "warnings"}
    assert data["version"] == 1
    assert data["warnings"] == []
    changeset = data["changeset"]
    assert (changeset["version"], changeset["source"], changeset["reason"]) == (1, "api", "winter")
    assert (changeset["actor"], changeset["actor_source"], changeset["client"]) == ("anonymous", "asserted", "")
    assert changeset["item_count"] == 2
    assert changeset["items"][0] == {"key": "pricing.vat_rate", "old_value": None, "new_value": 0.2, "reset": False}
    assert State.objects.get().current_version == 1
    assert TunableValue.objects.get(key="thermostat.mode").value == "heat"


def test_validation_problem_reports_every_error(api: APIClient) -> None:
    body = changes(
        {"key": "pricing.discount", "value": 1},
        {"key": "pricing.vat_rate", "value": "abc"},
        {"key": "thermostat.target_c", "value": 100.0},
        {"key": "weights.alpha", "value": 0.9},
    )
    response = post(api, "changesets/", body)
    assert response.status_code == 422
    assert response["Content-Type"] == PROBLEM
    assert response["X-Tunables-Version"] == "0"
    assert response.json() == {
        "type": "urn:tunables:problem:validation-failed",
        "title": "Validation failed",
        "status": 422,
        "detail": "4 errors",
        "errors": [
            {"key": "pricing.discount", "code": "unknown_key", "detail": "unknown tunable 'pricing.discount'"},
            {"key": "pricing.vat_rate", "code": "type", "detail": "expected a number"},
            {"key": "thermostat.target_c", "code": "max", "detail": "must be <= 30.0"},
            {"group": "weights", "code": "group", "detail": "weights must sum to 1"},
        ],
    }
    assert counts() == (0, 0)


def test_if_match(api: APIClient) -> None:
    apply(Change("pricing.vat_rate", 0.2))
    body = changes({"key": "pricing.vat_rate", "value": 0.1})
    response = post(api, "changesets/", body, HTTP_IF_MATCH='"0"')
    assert response.status_code == 412
    assert response["Content-Type"] == PROBLEM
    assert response["X-Tunables-Version"] == "1"
    assert response.json() == {
        "type": "urn:tunables:problem:version-conflict",
        "title": "Version conflict",
        "status": 412,
        "detail": "expected version 0, current version is 1",
        "expected_version": 0,
        "current_version": 1,
    }
    assert post(api, "changesets/", body, HTTP_IF_MATCH='"1"').status_code == 201
    assert (
        post(api, "changesets/", changes({"key": "pricing.vat_rate", "value": 0.3}), HTTP_IF_MATCH="*").status_code
        == 201
    )
    response = post(api, "changesets/", body, HTTP_IF_MATCH="latest")
    assert response.status_code == 400
    assert response.json()["type"] == "urn:tunables:problem:invalid"
    assert "If-Match" in response.json()["errors"]


@pytest.mark.parametrize("path", ["changesets/", "validate/"])
def test_dry_run(api: APIClient, path: str) -> None:
    extra = {"dry_run": True} if path == "changesets/" else {}
    response = post(api, path, changes({"key": "thermostat.legacy_offset", "value": 1.5}, **extra))
    assert response.status_code == 200
    assert response.json() == {
        "valid": True,
        "warnings": [
            {"key": "thermostat.legacy_offset", "code": "deprecated", "detail": "deprecated: Use target_c instead."}
        ],
    }
    assert counts() == (0, 0)
    response = post(api, path, changes({"key": "pricing.vat_rate", "value": 5.0}, **extra))
    assert response.status_code == 422
    response = post(api, path, changes({"key": "pricing.vat_rate", "value": 0.24}, **extra))
    assert response.status_code == 400
    assert response.json()["type"] == "urn:tunables:problem:nothing-to-change"
    assert counts() == (0, 0)


def test_deprecated_key_warns_on_real_write(api: APIClient) -> None:
    response = post(api, "changesets/", changes({"key": "thermostat.legacy_offset", "value": 1.5}))
    assert response.status_code == 201
    assert response.json()["warnings"][0]["code"] == "deprecated"


def test_null_value_and_reset(api: APIClient) -> None:
    response = post(api, "changesets/", changes({"key": "pricing.vat_rate", "value": None}))
    assert response.status_code == 422
    assert response.json()["errors"] == [{"key": "pricing.vat_rate", "code": "type", "detail": "expected a number"}]
    response = post(api, "changesets/", changes({"key": "pricing.vat_rate"}))
    assert response.status_code == 422
    apply(Change("pricing.vat_rate", 0.2))
    response = post(api, "changesets/", changes({"key": "pricing.vat_rate", "reset": True}))
    assert response.status_code == 201
    assert response.json()["changeset"]["items"][0]["reset"] is True
    assert not TunableValue.objects.filter(key="pricing.vat_rate").exists()


def test_empty_and_malformed_bodies(api: APIClient) -> None:
    response = post(api, "changesets/", changes())
    assert response.status_code == 400
    assert response.json()["type"] == "urn:tunables:problem:nothing-to-change"
    response = post(api, "changesets/", {"reason": "x"})
    assert response.status_code == 400
    assert response["Content-Type"] == PROBLEM
    body = response.json()
    assert body["type"] == "urn:tunables:problem:invalid"
    assert "changes" in body["errors"]
    response = post(api, "changesets/", changes({"value": 1}))
    assert response.status_code == 400
    assert counts() == (0, 0)


def resolver(request: Any) -> Actor:
    return Actor("resolved", "system", client="custom")


def test_actor_resolution(api: APIClient) -> None:
    body = changes({"key": "pricing.vat_rate", "value": 0.2})

    def actor_of(response: Any) -> tuple[str, str, str]:
        cs = response.json()["changeset"]
        return cs["actor"], cs["actor_source"], cs["client"]

    assert actor_of(post(api, "changesets/", body, HTTP_X_TUNABLES_CLIENT="script")) == (
        "anonymous",
        "asserted",
        "script",
    )
    body["changes"][0]["value"] = 0.3
    assert actor_of(post(api, "changesets/", body, HTTP_X_TUNABLES_ACTOR="ops", HTTP_X_TUNABLES_CLIENT="cli")) == (
        "ops",
        "asserted",
        "cli",
    )
    api.force_authenticate(User.objects.create_user("alice"))
    body["changes"][0]["value"] = 0.4
    assert actor_of(post(api, "changesets/", body, HTTP_X_TUNABLES_ACTOR="ignored", HTTP_X_TUNABLES_CLIENT="web")) == (
        "alice",
        "verified",
        "web",
    )
    api.force_authenticate(None)
    body["changes"][0]["value"] = 0.5
    with settings_with(ACTOR_HEADER="X-Who", CLIENT_HEADER="X-From"):
        assert actor_of(post(api, "changesets/", body, HTTP_X_WHO="bob", HTTP_X_FROM="curl")) == (
            "bob",
            "asserted",
            "curl",
        )
    body["changes"][0]["value"] = 0.6
    with settings_with(ACTOR_RESOLVER=f"{__name__}.resolver"):
        assert actor_of(post(api, "changesets/", body)) == ("resolved", "system", "custom")


def test_request_id(api: APIClient) -> None:
    body = changes({"key": "pricing.vat_rate", "value": 0.2})
    assert post(api, "changesets/", body, HTTP_X_REQUEST_ID="req-1").json()["changeset"]["request_id"] == "req-1"
    body["changes"][0]["value"] = 0.3
    assert post(api, "changesets/", body).json()["changeset"]["request_id"] == ""
    body["changes"][0]["value"] = 0.4
    with settings_with(REQUEST_ID_HEADER="X-Trace"):
        assert post(api, "changesets/", body, HTTP_X_TRACE="t-9").json()["changeset"]["request_id"] == "t-9"
