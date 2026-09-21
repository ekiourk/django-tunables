from typing import Any

import pytest
from django.contrib.auth.models import Permission, User
from django.test import override_settings
from rest_framework.test import APIClient

from tunables.sync import SyncResult

pytestmark = pytest.mark.django_db

BASE = "/api/tunables/"
ALIGNED = override_settings(
    TUNABLES={
        "CATALOGUE": "tests.catalogue.catalogue",
        "API_PERMISSION_CLASSES": ["tunables.api.permissions.TunablesPermissions"],
    }
)


def client_with(*codenames: str) -> APIClient:
    user = User.objects.create_user(f"u{len(codenames)}{''.join(codenames)}"[:30])
    for codename in codenames:
        user.user_permissions.add(Permission.objects.get(content_type__app_label="tunables", codename=codename))
    api = APIClient()
    api.force_authenticate(user)
    return api


def change(api: APIClient) -> Any:
    body = {"changes": [{"key": "pricing.vat_rate", "value": 0.3}], "reason": "r"}
    return api.post(BASE + "changesets/", body, format="json")


@ALIGNED
def test_a_read_needs_the_view_permission(synced: SyncResult) -> None:
    assert client_with().get(BASE + "groups/").status_code == 403
    assert client_with("view_tunabledefinition").get(BASE + "groups/").status_code == 200


@ALIGNED
def test_a_value_write_needs_the_change_set_permission(synced: SyncResult) -> None:
    assert change(client_with("view_tunabledefinition")).status_code == 403
    assert change(client_with("view_tunabledefinition", "add_changeset")).status_code == 201


@ALIGNED
def test_a_tag_write_needs_the_tag_permissions(synced: SyncResult) -> None:
    writer = client_with("view_tunabledefinition", "add_changeset")
    assert (
        writer.put(BASE + "definitions/pricing.vat_rate/tags/", {"tags": ["review"]}, format="json").status_code == 403
    )
    tagger = client_with("view_tunabledefinition", "change_tag")
    assert (
        tagger.put(BASE + "definitions/pricing.vat_rate/tags/", {"tags": ["review"]}, format="json").status_code == 200
    )
    assert tagger.post(BASE + "tags/", {"name": "other"}, format="json").status_code == 403
    assert (
        client_with("view_tunabledefinition", "add_tag")
        .post(BASE + "tags/", {"name": "other"}, format="json")
        .status_code
        == 201
    )


@ALIGNED
def test_a_tagger_cannot_change_values(synced: SyncResult) -> None:
    assert change(client_with("view_tunabledefinition", "change_tag", "add_tag")).status_code == 403


@ALIGNED
def test_an_anonymous_request_is_refused(synced: SyncResult) -> None:
    response = APIClient().get(BASE + "groups/")
    assert response.status_code == 403
    # No authenticator issues a challenge, so DRF reports this as not authenticated.
    assert response.json()["type"] == "urn:tunables:problem:not-authenticated"


def test_the_class_is_not_active_unless_configured(api: APIClient) -> None:
    assert api.get(BASE + "groups/").status_code == 200
