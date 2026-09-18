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


def test_patch_group_values(api: APIClient) -> None:
    apply(Change("pricing.currencies", ["USD"]))
    response = api.patch(BASE + "groups/pricing/values/", {"vat_rate": 0.2, "currencies": None}, format="json")
    assert response.status_code == 201
    data = response.json()
    assert data["version"] == 2
    assert data["changeset"]["source"] == "api"
    assert data["changeset"]["items"] == [
        {"key": "pricing.currencies", "old_value": ["USD"], "new_value": None, "reset": True},
        {"key": "pricing.vat_rate", "old_value": None, "new_value": 0.2, "reset": False},
    ]
    assert api.patch(BASE + "groups/shop/values/", {"x": 1}, format="json").status_code == 404
    response = api.patch(BASE + "groups/pricing/values/", {"discount": 1}, format="json")
    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "unknown_key"
    response = api.patch(BASE + "groups/pricing/values/", [1, 2], format="json")
    assert response.status_code == 400
    assert response.json()["type"] == "urn:tunables:problem:invalid"
    response = api.patch(BASE + "groups/pricing/values/", {"vat_rate": 0.1}, format="json", HTTP_IF_MATCH='"1"')
    assert response.status_code == 412


def test_rollback(api: APIClient) -> None:
    apply(Change("pricing.vat_rate", 0.2), Change("thermostat.mode", "heat"))
    apply(Change("pricing.vat_rate", 0.1), Change("pricing.currencies", ["USD"]))
    response = post(api, "rollback/", {"to_version": 1, "reason": "undo"})
    assert response.status_code == 201
    changeset = response.json()["changeset"]
    assert (changeset["version"], changeset["source"], changeset["restores_version"]) == (3, "rollback", 1)
    assert changeset["reason"] == "undo"
    assert (changeset["actor"], changeset["actor_source"]) == ("anonymous", "asserted")
    assert {item["key"] for item in changeset["items"]} == {"pricing.vat_rate", "pricing.currencies"}
    response = post(api, "rollback/", {"to_version": 9})
    assert response.status_code == 422
    assert response.json() == {
        "type": "urn:tunables:problem:unknown-version",
        "title": "Unknown version",
        "status": 422,
        "detail": "no snapshot for version 9",
        "version": 9,
    }
    response = post(api, "rollback/", {"to_version": 3})
    assert response.status_code == 400
    assert response.json()["type"] == "urn:tunables:problem:nothing-to-change"
    assert post(api, "rollback/", {"to_version": 0}, HTTP_IF_MATCH='"1"').status_code == 412
    assert post(api, "rollback/", {"to_version": 0}, HTTP_IF_MATCH='"3"').status_code == 201
    assert post(api, "rollback/", {"to_version": -1}).status_code == 400
    assert post(api, "rollback/", {}).status_code == 400


def document(**values: Any) -> dict[str, Any]:
    body = api_document()
    for dotted, value in values.items():
        group, _, name = dotted.partition("__")
        body["groups"].setdefault(group, {})[name] = value
    return body


def api_document() -> dict[str, Any]:
    from tunables.services import latest_snapshot

    return dict(latest_snapshot().document)


def test_import(api: APIClient) -> None:
    response = post(api, "import/", document(pricing__vat_rate=0.2, pricing__discount=5, shop__open=True))
    assert response.status_code == 201
    data = response.json()
    assert data["changeset"]["source"] == "import"
    assert [item["key"] for item in data["changeset"]["items"]] == ["pricing.vat_rate"]
    assert data["warnings"] == [
        {"key": "pricing.discount", "code": "unknown_key", "detail": "unknown tunable 'pricing.discount'"},
        {"key": "shop.open", "code": "unknown_key", "detail": "unknown tunable 'shop.open'"},
    ]
    response = post(api, "import/?strict=1", document(pricing__vat_rate=0.3, pricing__discount=5))
    assert response.status_code == 422
    assert response.json()["errors"] == [
        {"key": "pricing.discount", "code": "unknown_key", "detail": "unknown tunable 'pricing.discount'"}
    ]
    response = post(api, "import/", {**document(), "format_version": 2})
    assert response.status_code == 422
    assert response.json()["errors"][0]["key"] == "format_version"
    response = post(api, "import/", document())
    assert response.status_code == 400
    assert response.json()["type"] == "urn:tunables:problem:nothing-to-change"
    response = post(api, "import/", [1])
    assert response.status_code == 400
    assert response.json()["type"] == "urn:tunables:problem:invalid"
    assert counts() == (1, 1)


def only_pricing(request: Any) -> set[str]:
    return {"pricing"}


def everything(request: Any) -> None:
    return None


def test_editable_groups(api: APIClient) -> None:
    apply(Change("thermostat.mode", "heat"))
    forbidden = {
        "type": "urn:tunables:problem:forbidden-group",
        "title": "Forbidden group",
        "status": 403,
        "detail": "group 'thermostat' is not editable by this request",
        "group": "thermostat",
    }
    with settings_with(EDITABLE_GROUPS=f"{__name__}.only_pricing"):
        assert post(api, "changesets/", changes({"key": "pricing.vat_rate", "value": 0.2})).status_code == 201
        before = counts()
        response = post(api, "changesets/", changes({"key": "thermostat.mode", "value": "cool"}))
        assert response.status_code == 403
        assert response["Content-Type"] == PROBLEM
        assert response.json() == forbidden
        assert api.patch(BASE + "groups/thermostat/values/", {"mode": "cool"}, format="json").status_code == 403
        assert post(api, "rollback/", {"to_version": 0}).json() == forbidden
        assert post(api, "import/", document(thermostat__mode="cool")).status_code == 403
        assert (
            post(api, "changesets/", changes({"key": "thermostat.mode", "value": "cool"}, dry_run=True)).status_code
            == 403
        )
        assert post(api, "validate/", changes({"key": "thermostat.mode", "value": "cool"})).status_code == 403
        assert counts() == before
        assert post(api, "rollback/", {"to_version": 1}).status_code == 201
    with settings_with(EDITABLE_GROUPS=everything):
        assert post(api, "changesets/", changes({"key": "thermostat.mode", "value": "cool"})).status_code == 201


def test_patch_reason_header(api: APIClient) -> None:
    response = api.patch(
        BASE + "groups/pricing/values/", {"vat_rate": 0.2}, format="json", HTTP_X_TUNABLES_REASON="spring sale"
    )
    assert response.status_code == 201
    assert response.json()["changeset"]["reason"] == "spring sale"
    response = api.patch(BASE + "groups/pricing/values/", {"vat_rate": 0.1}, format="json")
    assert response.json()["changeset"]["reason"] == ""


def test_patch_with_the_default_value_resets_an_override(api: APIClient) -> None:
    apply(Change("pricing.vat_rate", 0.2))
    response = api.patch(BASE + "groups/pricing/values/", {"vat_rate": 0.24}, format="json")
    assert response.status_code == 201
    assert response.json()["changeset"]["items"] == [
        {"key": "pricing.vat_rate", "old_value": 0.2, "new_value": None, "reset": True}
    ]
    assert api.get(BASE + "values/").json()["overridden"] == []


@pytest.mark.parametrize("path", ["changesets/", "validate/"])
def test_dry_run_honours_if_match(api: APIClient, path: str) -> None:
    apply(Change("pricing.vat_rate", 0.2))
    extra = {"dry_run": True} if path == "changesets/" else {}
    body = changes({"key": "pricing.vat_rate", "value": 0.1}, **extra)
    response = post(api, path, body, HTTP_IF_MATCH='"0"')
    assert response.status_code == 412
    assert response.json()["type"] == "urn:tunables:problem:version-conflict"
    assert (response.json()["expected_version"], response.json()["current_version"]) == (0, 1)
    response = post(api, path, body, HTTP_IF_MATCH='"1"')
    assert response.status_code == 200
    assert response.json()["valid"] is True
    invalid = changes({"key": "pricing.vat_rate", "value": 5.0}, **extra)
    assert post(api, path, invalid, HTTP_IF_MATCH='"0"').status_code == 412
    assert counts() == (1, 1)


def test_patch_reason_header_name_is_a_setting(api: APIClient) -> None:
    with settings_with(REASON_HEADER="X-Why"):
        response = api.patch(
            BASE + "groups/pricing/values/",
            {"vat_rate": 0.2},
            format="json",
            HTTP_X_WHY="renamed header",
            HTTP_X_TUNABLES_REASON="ignored",
        )
    assert response.status_code == 201
    assert response.json()["changeset"]["reason"] == "renamed header"


def test_catalogue_validation_error_in_the_problem_body(api: APIClient) -> None:
    apply(Change("pricing.currencies", ["EUR", "USD"]))
    response = post(api, "changesets/", changes({"key": "limits.max_currencies", "value": 1}))
    assert response.status_code == 422
    assert response.json()["errors"] == [
        {"scope": "catalogue", "code": "catalogue", "detail": "accepted currencies exceed limits.max_currencies"}
    ]


def test_import_replace_mode(api: APIClient) -> None:
    apply(Change("pricing.vat_rate", 0.2), Change("thermostat.mode", "heat"))
    body = {"format_version": 1, "groups": {"pricing": {"vat_rate": 0.1}}}
    response = post(api, "import/?mode=replace", body)
    assert response.status_code == 201
    assert {i["key"]: i["reset"] for i in response.json()["changeset"]["items"]} == {
        "pricing.vat_rate": False,
        "thermostat.mode": True,
    }
    response = post(api, "import/?mode=merge", body)
    assert response.status_code == 400
    assert response.json()["type"] == "urn:tunables:problem:invalid"
    apply(Change("thermostat.mode", "cool"))
    with settings_with(EDITABLE_GROUPS=f"{__name__}.only_pricing"):
        response = post(api, "import/?mode=replace", {"format_version": 1, "groups": {"pricing": {"vat_rate": 0.3}}})
    assert response.status_code == 403
    assert response.json()["group"] == "thermostat"


def test_changeset_metadata_round_trips(api: APIClient) -> None:
    body = changes({"key": "pricing.vat_rate", "value": 0.2}, metadata={"ticket": "OPS-12", "batch": 3})
    response = post(api, "changesets/", body)
    assert response.status_code == 201
    assert response.json()["changeset"]["metadata"] == {"ticket": "OPS-12", "batch": 3}
    assert api.get(BASE + "changesets/1/").json()["metadata"] == {"ticket": "OPS-12", "batch": 3}
    assert api.get(BASE + "changesets/").json()["results"][0]["metadata"] == {"ticket": "OPS-12", "batch": 3}
    assert ChangeSet.objects.get(version=1).metadata == {"ticket": "OPS-12", "batch": 3}
    response = post(api, "changesets/", changes({"key": "pricing.vat_rate", "value": 0.3}))
    assert response.json()["changeset"]["metadata"] == {}


@pytest.mark.parametrize("bad", [["a"], "note", 7], ids=["list", "string", "number"])
def test_changeset_metadata_must_be_an_object(api: APIClient, bad: Any) -> None:
    response = post(api, "changesets/", changes({"key": "pricing.vat_rate", "value": 0.2}, metadata=bad))
    assert response.status_code == 400
    assert response.json()["type"] == "urn:tunables:problem:invalid"
    assert "metadata" in response.json()["errors"]
    assert counts() == (0, 0)


def test_dry_run_ignores_metadata(api: APIClient) -> None:
    body = changes({"key": "pricing.vat_rate", "value": 0.2}, dry_run=True, metadata={"ticket": "OPS-12"})
    response = post(api, "changesets/", body)
    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert counts() == (0, 0)


def test_tag_create_update_delete(api: APIClient) -> None:
    before = ChangeSet.objects.count()
    response = post(api, "tags/", {"name": "review", "description": "Needs a second look."})
    assert response.status_code == 201
    assert response.json() == {
        "name": "review",
        "description": "Needs a second look.",
        "from_catalogue": False,
        "definition_count": 0,
    }
    assert response["X-Tunables-Version"] == "0"
    response = post(api, "tags/", {"name": "review"})
    assert response.status_code == 409
    assert response.json() == {
        "type": "urn:tunables:problem:tag-exists",
        "title": "Tag exists",
        "status": 409,
        "detail": "tag 'review' already exists",
        "name": "review",
    }
    response = post(api, "tags/", {"name": "Bad Name"})
    assert response.status_code == 400
    assert "name" in response.json()["errors"]
    response = api.patch(BASE + "tags/review/", {"description": "Checked."}, format="json")
    assert response.status_code == 200
    assert response.json()["description"] == "Checked."
    assert api.patch(BASE + "tags/nope/", {"description": "x"}, format="json").status_code == 404
    assert api.delete(BASE + "tags/review/").status_code == 204
    assert api.delete(BASE + "tags/review/").status_code == 404
    response = api.delete(BASE + "tags/money/")
    assert response.status_code == 409
    assert response.json()["type"] == "urn:tunables:problem:tag-seeded"
    assert ChangeSet.objects.count() == before


def test_put_definition_tags(api: APIClient) -> None:
    response = api.put(BASE + "definitions/weights.beta/tags/", {"tags": ["review", "q3"]}, format="json")
    assert response.status_code == 200
    assert response.json() == {"key": "weights.beta", "tags": ["q3", "review"]}
    response = api.put(BASE + "definitions/weights.beta/tags/", {"tags": ["review"]}, format="json")
    assert response.json()["tags"] == ["review"]
    response = api.put(BASE + "definitions/pricing.vat_rate/tags/", {"tags": []}, format="json")
    assert response.json()["tags"] == ["money"]
    assert api.put(BASE + "definitions/weights.beta/tags/", {"tags": ["Bad"]}, format="json").status_code == 400
    assert api.put(BASE + "definitions/weights.beta/tags/", {"tags": "review"}, format="json").status_code == 400
    assert api.put(BASE + "definitions/weights.nope/tags/", {"tags": []}, format="json").status_code == 404
    with settings_with(EDITABLE_GROUPS=f"{__name__}.only_pricing"):
        response = api.put(BASE + "definitions/weights.beta/tags/", {"tags": []}, format="json")
        assert response.status_code == 403
        assert response.json()["group"] == "weights"
        assert api.put(BASE + "definitions/pricing.vat_rate/tags/", {"tags": ["x"]}, format="json").status_code == 200
    assert ChangeSet.objects.count() == 0
    assert api.get(BASE + "values/")["X-Tunables-Version"] == "0"


def test_tag_writes_require_sync(api: APIClient) -> None:
    State.objects.update(catalogue_version="sha256:stale")
    assert post(api, "tags/", {"name": "review"}).status_code == 503
    assert api.put(BASE + "definitions/weights.beta/tags/", {"tags": []}, format="json").status_code == 503


def test_check_group_editable_helper(api: APIClient) -> None:
    from django.test import RequestFactory

    from tunables.access import check_group_editable
    from tunables.errors import GroupNotEditable

    request = RequestFactory().get("/")
    check_group_editable(request, "thermostat")
    with settings_with(EDITABLE_GROUPS=f"{__name__}.only_pricing"):
        check_group_editable(request, "pricing")
        with pytest.raises(GroupNotEditable) as info:
            check_group_editable(request, "thermostat")
    assert info.value.group == "thermostat"
