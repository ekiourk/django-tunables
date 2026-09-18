import json
from typing import Any

import pytest
from django.contrib.auth.models import Permission, User
from django.contrib.messages import get_messages
from django.test import Client

from tests.catalogue import catalogue
from tests.test_services import apply
from tunables import Change
from tunables.models import ChangeSet, State, TunableValue
from tunables.services import latest_snapshot
from tunables.sync import SyncResult

pytestmark = pytest.mark.django_db

INDEX = "/admin/tunables/tunabledefinition/"


def edit_url(group: str) -> str:
    return f"{INDEX}edit/{group}/"


def form_data(group: str, **overrides: Any) -> dict[str, Any]:
    """A full POST for the group's edit form built from the current effective values."""
    document = latest_snapshot().document
    data: dict[str, Any] = {"reason": "because", "expected_version": document["version"]}
    for tunable in catalogue.groups[group].tunables:
        value = document["groups"][group][tunable.name]
        if isinstance(value, bool):
            if value:
                data[tunable.name] = "on"
        elif isinstance(value, list | dict):
            data[tunable.name] = json.dumps(value)
        else:
            data[tunable.name] = str(value)
    data.update(overrides)
    return data


def messages_of(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def staff(*perms: str) -> Client:
    user, _ = User.objects.get_or_create(username="staff", defaults={"is_staff": True})
    user.user_permissions.clear()
    for perm in perms:
        app, codename = perm.split(".")
        user.user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=codename))
    client = Client()
    client.force_login(user)
    return client


def test_group_index(admin_client: Client, synced: SyncResult) -> None:
    response = admin_client.get(INDEX)
    assert response.status_code == 200
    content = response.content.decode()
    for title in ("Pricing", "Thermostat", "Weights"):
        assert title in content
    assert edit_url("pricing") in content
    assert "Prices and shipping rules for the web shop." in content
    assert [row["name"] for row in response.context["groups"]] == ["pricing", "thermostat", "weights", "limits"]
    assert "must not exceed limits.max_currencies" in content
    assert response.context["groups"][0]["count"] == 5
    assert INDEX in admin_client.get("/admin/").content.decode()


def test_edit_form_shows_current_values_and_reset_boxes(admin_client: Client, synced: SyncResult) -> None:
    apply(Change("pricing.vat_rate", 0.2))
    response = admin_client.get(edit_url("pricing"))
    assert response.status_code == 200
    form = response.context["form"]
    assert form.fields["vat_rate"].initial == 0.2
    assert form.fields["free_shipping_over"].initial == 50.0
    assert form.fields["currencies"].initial == ["EUR"]
    assert form.fields["vat_rate"].label == "VAT rate"
    assert "EUR" in form.fields["free_shipping_over"].help_text
    assert "reset_vat_rate" in form.fields
    assert "reset_free_shipping_over" not in form.fields
    assert form.fields["reason"].required is True
    assert form.fields["expected_version"].initial == 1
    content = response.content.decode()
    assert 'name="vat_rate"' in content
    assert 'name="reset_vat_rate"' in content
    assert 'name="expected_version" value="1"' in content


def test_valid_submit_creates_admin_changeset(admin_client: Client, synced: SyncResult) -> None:
    response = admin_client.post(edit_url("pricing"), form_data("pricing", vat_rate="0.2", reason="winter"))
    assert response.status_code == 302
    assert response["Location"] == INDEX
    changeset = ChangeSet.objects.get(version=1)
    assert (changeset.source, changeset.actor, changeset.actor_source, changeset.reason) == (
        "admin",
        "admin",
        "verified",
        "winter",
    )
    assert [item.key for item in changeset.items.all()] == ["pricing.vat_rate"]
    assert TunableValue.objects.get(key="pricing.vat_rate").value == 0.2
    assert any("version 1" in m for m in messages_of(response))


def test_reset_box_resets(admin_client: Client, synced: SyncResult) -> None:
    apply(Change("pricing.vat_rate", 0.2))
    response = admin_client.post(edit_url("pricing"), form_data("pricing", reset_vat_rate="on"))
    assert response.status_code == 302
    item = ChangeSet.objects.get(version=2).items.get()
    assert (item.key, item.reset, item.old_value) == ("pricing.vat_rate", True, 0.2)
    assert not TunableValue.objects.filter(key="pricing.vat_rate").exists()


def test_invalid_submit_maps_errors(admin_client: Client, synced: SyncResult) -> None:
    response = admin_client.post(edit_url("pricing"), form_data("pricing", vat_rate="5"))
    assert response.status_code == 200
    assert "vat_rate" in response.context["form"].errors
    response = admin_client.post(edit_url("weights"), form_data("weights", alpha="0.9"))
    assert response.status_code == 200
    assert response.context["form"].non_field_errors() == ["weights must sum to 1"]
    assert ChangeSet.objects.count() == 0


def test_missing_reason_and_emptied_value(admin_client: Client, synced: SyncResult) -> None:
    response = admin_client.post(edit_url("pricing"), form_data("pricing", vat_rate="0.2", reason=""))
    assert response.status_code == 200
    assert "reason" in response.context["form"].errors
    response = admin_client.post(edit_url("pricing"), form_data("pricing", vat_rate=""))
    assert response.status_code == 200
    assert "vat_rate" in response.context["form"].errors
    assert ChangeSet.objects.count() == 0


def test_unchanged_submit_is_an_error(admin_client: Client, synced: SyncResult) -> None:
    response = admin_client.post(edit_url("pricing"), form_data("pricing"))
    assert response.status_code == 200
    assert "nothing" in response.context["form"].non_field_errors()[0].lower()
    assert ChangeSet.objects.count() == 0


def test_stale_version_rerenders_with_fresh_values(admin_client: Client, synced: SyncResult) -> None:
    data = form_data("pricing", vat_rate="0.1")
    apply(Change("pricing.vat_rate", 0.2))
    response = admin_client.post(edit_url("pricing"), data)
    assert response.status_code == 200
    form = response.context["form"]
    assert form.is_bound is False
    assert form.fields["vat_rate"].initial == 0.2
    assert form.fields["expected_version"].initial == 1
    assert any("version 0" in m and "version 1" in m for m in messages_of(response))
    assert ChangeSet.objects.count() == 1


def test_unknown_group_and_out_of_sync(admin_client: Client, synced: SyncResult) -> None:
    assert admin_client.get(edit_url("shop")).status_code == 404
    State.objects.update(catalogue_version="sha256:stale")
    response = admin_client.get(edit_url("pricing"))
    assert response.status_code == 302
    assert response["Location"] == INDEX
    assert any("tunables_sync" in m for m in messages_of(response))
    response = admin_client.get(INDEX)
    assert response.status_code == 200
    assert response.context["synced"] is False
    assert "tunables_sync" in response.content.decode()


def test_permissions(synced: SyncResult) -> None:
    viewer = staff("tunables.view_tunabledefinition")
    assert viewer.get(INDEX).status_code == 200
    assert viewer.get(edit_url("pricing")).status_code == 403
    assert viewer.post(edit_url("pricing"), form_data("pricing", vat_rate="0.2")).status_code == 403
    assert ChangeSet.objects.count() == 0
    editor = staff("tunables.view_tunabledefinition", "tunables.add_changeset")
    assert editor.get(edit_url("pricing")).status_code == 200
    assert editor.post(edit_url("pricing"), form_data("pricing", vat_rate="0.2")).status_code == 302
    assert ChangeSet.objects.get(version=1).actor == "staff"
    nobody = staff()
    assert nobody.get(INDEX).status_code == 403


CHANGESETS = "/admin/tunables/changeset/"
SNAPSHOTS = "/admin/tunables/snapshot/"


def test_changeset_admin_is_read_only(admin_client: Client, synced: SyncResult) -> None:
    result = apply(Change("pricing.vat_rate", 0.2), Change("thermostat.mode", "heat"), reason="winter")
    response = admin_client.get(CHANGESETS)
    assert response.status_code == 200
    content = response.content.decode()
    for text in ("winter", "alice", "api", ">2<"):
        assert text in content
    detail = f"{CHANGESETS}{result.changeset.pk}/change/"
    response = admin_client.get(detail)
    assert response.status_code == 200
    content = response.content.decode()
    assert "pricing.vat_rate" in content
    assert "0.2" in content
    assert "thermostat.mode" in content
    assert admin_client.post(detail, {"reason": "edited"}).status_code == 403
    assert admin_client.get(f"{CHANGESETS}add/").status_code == 403
    assert ChangeSet.objects.get(version=1).reason == "winter"


def test_rollback_action(admin_client: Client, synced: SyncResult) -> None:
    first = apply(Change("pricing.vat_rate", 0.2))
    apply(Change("pricing.vat_rate", 0.1), Change("thermostat.mode", "heat"))
    select = {"action": "rollback", "_selected_action": [first.changeset.pk], "index": "0"}
    response = admin_client.post(CHANGESETS, select)
    assert response.status_code == 200
    content = response.content.decode()
    assert 'name="reason"' in content
    assert "version 1" in content
    confirm = {"action": "rollback", "_selected_action": [first.changeset.pk], "confirm": "1", "reason": ""}
    response = admin_client.post(CHANGESETS, confirm)
    assert response.status_code == 200
    assert "reason" in response.content.decode().lower()
    assert ChangeSet.objects.count() == 2
    response = admin_client.post(CHANGESETS, {**confirm, "reason": "undo"})
    assert response.status_code == 302
    rolled = ChangeSet.objects.get(version=3)
    assert (rolled.source, rolled.restores_version, rolled.reason, rolled.actor) == ("rollback", 1, "undo", "admin")
    assert any("version 3" in m for m in messages_of(response))
    assert TunableValue.objects.get(key="pricing.vat_rate").value == 0.2
    assert not TunableValue.objects.filter(key="thermostat.mode").exists()
    response = admin_client.post(CHANGESETS, {**confirm, "reason": "again"})
    assert response.status_code == 302
    assert any("nothing" in m.lower() for m in messages_of(response))
    assert ChangeSet.objects.count() == 3
    both = {"action": "rollback", "_selected_action": [first.changeset.pk, rolled.pk], "index": "0"}
    response = admin_client.post(CHANGESETS, both)
    assert response.status_code == 302
    assert any("exactly one" in m for m in messages_of(response))
    assert ChangeSet.objects.count() == 3


def test_rollback_failure_is_reported(admin_client: Client, synced: SyncResult) -> None:
    first = apply(Change("pricing.vat_rate", 0.2))
    apply(Change("pricing.vat_rate", 0.1))
    State.objects.update(catalogue_version="sha256:stale")
    confirm = {"action": "rollback", "_selected_action": [first.changeset.pk], "confirm": "1", "reason": "undo"}
    response = admin_client.post(CHANGESETS, confirm)
    assert response.status_code == 302
    assert any(m.startswith("Rollback failed") and "tunables_sync" in m for m in messages_of(response))
    assert ChangeSet.objects.count() == 2


def test_rollback_action_requires_write_permission(synced: SyncResult) -> None:
    first = apply(Change("pricing.vat_rate", 0.2))
    viewer = staff("tunables.view_changeset")
    response = viewer.get(CHANGESETS)
    assert response.status_code == 200
    assert "Roll back to this version" not in response.content.decode()
    select = {"action": "rollback", "_selected_action": [first.changeset.pk], "confirm": "1", "reason": "x"}
    viewer.post(CHANGESETS, select)
    assert ChangeSet.objects.count() == 1


def test_snapshot_admin(admin_client: Client, synced: SyncResult) -> None:
    result = apply(Change("pricing.vat_rate", 0.2))
    response = admin_client.get(SNAPSHOTS)
    assert response.status_code == 200
    assert catalogue.version[:16] in response.content.decode()
    response = admin_client.get(f"{SNAPSHOTS}{result.snapshot.pk}/change/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "<pre" in content
    assert "format_version&quot;: 1" in content
    assert "pricing.vat_rate" in content
    assert admin_client.get(f"{SNAPSHOTS}add/").status_code == 403


def test_catalogue_validator_error_is_a_non_field_error(admin_client: Client, synced: SyncResult) -> None:
    apply(Change("pricing.currencies", ["EUR", "USD"]))
    response = admin_client.post(edit_url("limits"), form_data("limits", max_currencies="1"))
    assert response.status_code == 200
    assert response.context["form"].non_field_errors() == ["accepted currencies exceed limits.max_currencies"]
    assert ChangeSet.objects.count() == 1
