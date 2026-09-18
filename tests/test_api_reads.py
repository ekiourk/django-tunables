import json
from typing import Any
from urllib.parse import urlencode

import pytest
from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework.test import APIClient

from tests.catalogue import catalogue, pricing, thermostat
from tests.test_services import apply
from tunables import Actor, Change, services
from tunables.models import ChangeSet, Snapshot, State
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
            "tunable_count": 5,
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
        {"name": "limits", "title": "Limits", "description": "", "order": 4, "tunable_count": 1, "validators": []},
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
    assert list(body) == ["pricing", "thermostat", "weights", "limits"]
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


STORED_DATA_READS = [
    "values/",
    "groups/pricing/values/",
    "changesets/",
    "changesets/1/",
    "snapshots/latest/",
    "snapshots/1/",
    "export/",
]
CATALOGUE_READS = ["groups/", "groups/pricing/", "groups/pricing/schema/", "schema/", "definitions/"]
WRITES = [
    ("post", "changesets/", {"changes": [{"key": "pricing.vat_rate", "value": 0.3}]}),
    ("post", "validate/", {"changes": [{"key": "pricing.vat_rate", "value": 0.3}]}),
    ("patch", "groups/pricing/values/", {"vat_rate": 0.3}),
    ("post", "rollback/", {"to_version": 0}),
    ("post", "import/", {"format_version": 1, "groups": {"pricing": {"vat_rate": 0.3}}}),
]


@pytest.mark.parametrize("path", STORED_DATA_READS)
def test_stored_data_reads_work_while_out_of_sync(api: APIClient, path: str) -> None:
    apply(Change("pricing.vat_rate", 0.2))
    State.objects.update(catalogue_version="sha256:stale")
    response = api.get(BASE + path)
    assert response.status_code == 200
    assert response["X-Tunables-Version"] == "1"


@pytest.mark.parametrize(("method", "path", "body"), WRITES, ids=[w[1] for w in WRITES])
def test_writes_are_503_while_out_of_sync(api: APIClient, method: str, path: str, body: Any) -> None:
    State.objects.update(catalogue_version="sha256:stale")
    response = getattr(api, method)(BASE + path, body, format="json")
    assert response.status_code == 503
    assert response.json()["type"] == "urn:tunables:problem:catalogue-out-of-sync"
    assert ChangeSet.objects.count() == 0


@pytest.mark.parametrize("path", CATALOGUE_READS)
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


def two_changes() -> None:
    apply(Change("pricing.vat_rate", 0.2), reason="first")
    apply(Change("thermostat.mode", "heat"), Change("pricing.currencies", ["USD"]), reason="second")


def test_values(api: APIClient) -> None:
    two_changes()
    response = api.get(BASE + "values/")
    assert response.status_code == 200
    assert response["ETag"] == '"2"'
    assert response["X-Tunables-Version"] == "2"
    body = response.json()
    assert body["version"] == 2
    assert body["groups"]["pricing"]["vat_rate"] == 0.2
    assert body["groups"]["pricing"]["currencies"] == ["USD"]
    assert body["groups"]["thermostat"]["mode"] == "heat"
    assert body["groups"]["weights"] == {"alpha": 0.5, "beta": 0.3, "gamma": 0.2}
    assert body["overridden"] == ["pricing.currencies", "pricing.vat_rate", "thermostat.mode"]
    assert set(body) == {"version", "groups", "overridden"}


def test_values_not_modified(api: APIClient) -> None:
    two_changes()
    response = api.get(BASE + "values/", HTTP_IF_NONE_MATCH='"2"')
    assert response.status_code == 304
    assert response.content == b""
    assert response["ETag"] == '"2"'
    assert response["X-Tunables-Version"] == "2"
    assert api.get(BASE + "values/", HTTP_IF_NONE_MATCH='"1"').status_code == 200
    assert api.get(BASE + "values/", HTTP_IF_NONE_MATCH='"1", "2"').status_code == 304


def test_group_values(api: APIClient) -> None:
    two_changes()
    response = api.get(BASE + "groups/pricing/values/")
    assert response["ETag"] == '"2"'
    assert response.json() == {
        "version": 2,
        "values": {
            "vat_rate": 0.2,
            "free_shipping_over": 50.0,
            "currencies": ["USD"],
            "shipping_rates": {"EUR": 4.9},
            "allow_backorders": False,
        },
        "overridden": ["currencies", "vat_rate"],
    }
    assert api.get(BASE + "groups/pricing/values/", HTTP_IF_NONE_MATCH='"2"').status_code == 304
    assert api.get(BASE + "groups/shop/values/").status_code == 404


def test_changeset_list(api: APIClient) -> None:
    two_changes()
    body = api.get(BASE + "changesets/").json()
    assert set(body) == {"count", "next", "previous", "results"}
    assert body["count"] == 2
    assert [c["version"] for c in body["results"]] == [2, 1]
    second = body["results"][0]
    assert second["item_count"] == 2
    assert (second["actor"], second["actor_source"], second["client"]) == ("alice", "verified", "cli")
    assert (second["source"], second["reason"], second["restores_version"]) == ("api", "second", None)
    assert second["catalogue_version"] == catalogue.version
    assert "items" not in second
    assert second["created_at"].endswith("Z")


def test_changeset_pagination(api: APIClient) -> None:
    two_changes()
    apply(Change("weights.alpha", 0.6), Change("weights.beta", 0.2))
    with override_settings(TUNABLES={"CATALOGUE": CATALOGUE, "PAGE_SIZE": 2}):
        first = api.get(BASE + "changesets/").json()
        assert [c["version"] for c in first["results"]] == [3, 2]
        assert first["next"].endswith("changesets/?page=2")
        assert first["previous"] is None
        second = api.get(BASE + "changesets/?page=2").json()
        assert [c["version"] for c in second["results"]] == [1]
        assert second["next"] is None
        assert api.get(BASE + "changesets/?page=9").status_code == 404


def test_changeset_filters(api: APIClient) -> None:
    two_changes()
    services.apply_changeset(
        [Change("weights.alpha", 0.6), Change("weights.beta", 0.2)], actor=Actor("bob", "asserted"), source="api"
    )
    since = urlencode({"since": ChangeSet.objects.get(version=2).created_at.isoformat()})

    def versions(query: str) -> list[int]:
        response = api.get(BASE + "changesets/?" + query)
        assert response.status_code == 200
        return [c["version"] for c in response.json()["results"]]

    assert versions("group=pricing") == [2, 1]
    assert versions("group=weights") == [3]
    assert versions("key=pricing.vat_rate") == [1]
    assert versions("key=thermostat.mode") == [2]
    assert versions("actor=bob") == [3]
    assert versions("actor=nobody") == []
    assert versions(since) == [3, 2]
    assert versions("group=pricing&actor=alice") == [2, 1]
    response = api.get(BASE + "changesets/?since=yesterday")
    assert response.status_code == 400
    assert response["Content-Type"] == "application/problem+json"
    assert response.json()["type"] == "urn:tunables:problem:invalid"


def test_changeset_detail(api: APIClient) -> None:
    two_changes()
    body = api.get(BASE + "changesets/2/").json()
    assert body["version"] == 2
    assert body["item_count"] == 2
    assert body["items"] == [
        {"key": "pricing.currencies", "old_value": None, "new_value": ["USD"], "reset": False},
        {"key": "thermostat.mode", "old_value": None, "new_value": "heat", "reset": False},
    ]
    response = api.get(BASE + "changesets/9/")
    assert response.status_code == 404
    assert response.json()["detail"] == "unknown version 9"


def test_snapshots(api: APIClient) -> None:
    two_changes()
    latest = api.get(BASE + "snapshots/latest/")
    assert latest["ETag"] == '"2"'
    assert latest.json() == Snapshot.objects.get(version=2).document
    assert api.get(BASE + "snapshots/latest/", HTTP_IF_NONE_MATCH='"2"').status_code == 304
    first = api.get(BASE + "snapshots/1/")
    assert first["ETag"] == '"1"'
    assert first.json() == Snapshot.objects.get(version=1).document
    assert api.get(BASE + "snapshots/1/", HTTP_IF_NONE_MATCH='"1"').status_code == 304
    response = api.get(BASE + "snapshots/9/")
    assert response.status_code == 404
    assert response.json()["detail"] == "unknown version 9"


def test_export(api: APIClient) -> None:
    two_changes()
    response = api.get(BASE + "export/")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert response["Content-Disposition"] == 'attachment; filename="tunables-v2.json"'
    assert json.loads(response.content) == Snapshot.objects.get(version=2).document


@pytest.mark.parametrize(
    "path",
    [
        "groups/",
        "groups/pricing/",
        "groups/pricing/schema/",
        "groups/pricing/values/",
        "schema/",
        "definitions/",
        "values/",
        "changesets/",
        "changesets/1/",
        "snapshots/latest/",
        "snapshots/1/",
        "export/",
    ],
)
def test_every_read_endpoint_carries_the_version_header(api: APIClient, path: str) -> None:
    two_changes()
    response = api.get(BASE + path)
    assert response.status_code == 200
    assert response["X-Tunables-Version"] == "2"


def test_group_values_come_from_the_stored_document(api: APIClient) -> None:
    assert api.get(BASE + "groups/shop/values/").status_code == 404
    with override_settings(TUNABLES={"CATALOGUE": "tests.test_sync.reduced"}):
        response = api.get(BASE + "groups/pricing/values/")
    assert response.status_code == 200
    assert "allow_backorders" in response.json()["values"]


def test_status(api: APIClient) -> None:
    response = api.get(BASE + "status/")
    assert response.status_code == 200
    assert response["X-Tunables-Version"] == "0"
    assert response.json() == {
        "synced": True,
        "version": 0,
        "catalogue_version": catalogue.version,
        "code_catalogue_version": catalogue.version,
        "validators": ["The number of accepted currencies must not exceed limits.max_currencies."],
        "publishers": [],
    }
    State.objects.update(catalogue_version="sha256:stale")
    body = api.get(BASE + "status/").json()
    assert (body["synced"], body["catalogue_version"], body["code_catalogue_version"]) == (
        False,
        "sha256:stale",
        catalogue.version,
    )


def test_status_before_the_first_sync(db: None) -> None:
    response = APIClient().get(BASE + "status/")
    assert response.status_code == 200
    assert "X-Tunables-Version" not in response
    assert response.json() == {
        "synced": False,
        "version": None,
        "catalogue_version": None,
        "code_catalogue_version": catalogue.version,
        "validators": ["The number of accepted currencies must not exceed limits.max_currencies."],
        "publishers": [],
    }


def test_snapshot_schema_endpoint(api: APIClient) -> None:
    from tunables.schema import document_schema

    response = api.get(BASE + "snapshots/schema/")
    assert response.status_code == 200
    assert response.json() == document_schema(catalogue)
    State.objects.update(catalogue_version="sha256:stale")
    assert api.get(BASE + "snapshots/schema/").status_code == 503


def test_status_lists_publisher_states(api: APIClient) -> None:
    from tunables.models import PublisherState

    PublisherState.objects.create(publisher="tunables.publishers.FilePublisher", last_version=3, last_error="")
    PublisherState.objects.create(publisher="x.Broken", last_version=None, last_error="disk full")
    body = api.get(BASE + "status/").json()
    assert body["publishers"] == [
        {
            "publisher": "tunables.publishers.FilePublisher",
            "last_version": 3,
            "last_published_at": None,
            "last_error": "",
        },
        {"publisher": "x.Broken", "last_version": None, "last_published_at": None, "last_error": "disk full"},
    ]
