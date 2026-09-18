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


SEEN_REQUESTS: list[Any] = []


def only_pricing(request: Any) -> set[str]:
    SEEN_REQUESTS.append(request)
    return {"pricing"}


def restricted() -> Any:
    from django.test import override_settings

    return override_settings(
        TUNABLES={"CATALOGUE": "tests.catalogue.catalogue", "EDITABLE_GROUPS": f"{__name__}.only_pricing"}
    )


def test_index_marks_editable_groups_per_request(admin_client: Client, synced: SyncResult) -> None:
    with restricted():
        response = admin_client.get(INDEX)
    assert [(row["name"], row["can_edit"]) for row in response.context["groups"]] == [
        ("pricing", True),
        ("thermostat", False),
        ("weights", False),
        ("limits", False),
    ]
    assert response.content.decode().count(">Edit</a>") == 1


def test_edit_view_refuses_groups_the_request_may_not_edit(admin_client: Client, synced: SyncResult) -> None:
    with restricted():
        assert admin_client.get(edit_url("thermostat")).status_code == 403
        assert admin_client.post(edit_url("thermostat"), form_data("thermostat", mode="heat")).status_code == 403
        assert ChangeSet.objects.count() == 0
        assert admin_client.get(edit_url("pricing")).status_code == 200
        assert admin_client.post(edit_url("pricing"), form_data("pricing", vat_rate="0.2")).status_code == 302
    assert ChangeSet.objects.count() == 1


def test_rollback_action_respects_editable_groups(admin_client: Client, synced: SyncResult) -> None:
    first = apply(Change("pricing.vat_rate", 0.2))
    apply(Change("thermostat.mode", "heat"))
    third = apply(Change("pricing.vat_rate", 0.1))
    confirm = {"action": "rollback", "_selected_action": [first.changeset.pk], "confirm": "1", "reason": "undo"}
    with restricted():
        response = admin_client.post(CHANGESETS, confirm)
        assert response.status_code == 302
        assert any("thermostat" in m and "not editable" in m for m in messages_of(response))
        assert ChangeSet.objects.count() == 3
        confirm["_selected_action"] = [third.changeset.pk]
        apply(Change("pricing.vat_rate", 0.3))
        response = admin_client.post(CHANGESETS, confirm)
        assert response.status_code == 302
        assert any("Rolled back" in m for m in messages_of(response))
    assert ChangeSet.objects.get(version=5).restores_version == 3


def test_admin_passes_a_django_request_to_the_hook(admin_client: Client, synced: SyncResult) -> None:
    from django.http import HttpRequest

    SEEN_REQUESTS.clear()
    with restricted():
        admin_client.get(edit_url("pricing"))
    assert SEEN_REQUESTS and all(isinstance(r, HttpRequest) for r in SEEN_REQUESTS)
    assert SEEN_REQUESTS[0].user.get_username() == "admin"


def test_admin_form_labels_are_translated(admin_client: Client, synced: SyncResult, german: None) -> None:
    from django.utils import translation

    apply(Change("pricing.vat_rate", 0.2))
    with translation.override("de"):
        response = admin_client.get(edit_url("pricing"))
        assert response.status_code == 200
        # The label is a lazy string; it resolves in the language active when it is read.
        assert str(response.context["form"].fields["reset_vat_rate"].label) == "Auf Standard zurücksetzen"
    assert "Auf Standard zurücksetzen" in response.content.decode()
    assert str(response.context["form"].fields["reset_vat_rate"].label) == "Reset to default"


def test_group_index_shows_rule_violations(admin_client: Client, synced: SyncResult) -> None:
    from tests.test_sync import use

    response = admin_client.get(INDEX)
    assert response.context["violations"] == []
    assert "alpha must be below 0.4" not in response.content.decode()
    with use("strict_weights"):
        response = admin_client.get(INDEX)
    assert response.context["violations"] == ["group weights: group: alpha must be below 0.4"]
    content = response.content.decode()
    assert "alpha must be below 0.4" in content
    assert 'class="errornote"' in content


DEFINITIONS = f"{INDEX}definitions/"


def tags_url(key: str) -> str:
    return f"{DEFINITIONS}{key}/tags/"


def test_group_index_is_organised_by_category(admin_client: Client, synced: SyncResult) -> None:
    response = admin_client.get(INDEX)
    assert [(c["name"], [g["name"] for g in c["groups"]]) for c in response.context["categories"]] == [
        ("shop", ["pricing"]),
        ("building", ["thermostat"]),
        ("general", ["weights", "limits"]),
    ]
    content = response.content.decode()
    assert content.index("<h2>Shop</h2>") < content.index("<h2>Building</h2>") < content.index("<h2>General</h2>")
    assert "Everything else." in content


def test_definitions_page_lists_filters_and_searches(admin_client: Client, synced: SyncResult) -> None:
    response = admin_client.get(DEFINITIONS)
    assert response.status_code == 200
    rows = response.context["rows"]
    assert len(rows) == 14
    by_key = {row["key"]: row for row in rows}
    assert by_key["pricing.vat_rate"]["tags"] == ["money"]
    assert (by_key["weights.alpha"]["category"], by_key["weights.alpha"]["group"]) == ("general", "weights")
    assert [r["key"] for r in admin_client.get(DEFINITIONS + "?category=shop").context["rows"]] == [
        "pricing.vat_rate",
        "pricing.free_shipping_over",
        "pricing.currencies",
        "pricing.shipping_rates",
        "pricing.allow_backorders",
    ]
    assert [r["key"] for r in admin_client.get(DEFINITIONS + "?tag=money").context["rows"]] == [
        "pricing.vat_rate",
        "pricing.shipping_rates",
        "limits.max_currencies",
    ]
    assert [r["key"] for r in admin_client.get(DEFINITIONS + "?q=vat").context["rows"]] == ["pricing.vat_rate"]
    assert admin_client.get(DEFINITIONS + "?category=nope").status_code == 404
    assert admin_client.get(DEFINITIONS + "?group=nope").status_code == 404
    assert tags_url("pricing.vat_rate") in response.content.decode()
    viewer = staff("tunables.view_tunabledefinition")
    page = viewer.get(DEFINITIONS)
    assert page.status_code == 200
    assert tags_url("pricing.vat_rate") not in page.content.decode()
    assert staff().get(DEFINITIONS).status_code == 403


def test_definition_tags_page(admin_client: Client, synced: SyncResult) -> None:
    from tunables.models import Tag, TunableDefinitionTag

    response = admin_client.get(tags_url("pricing.vat_rate"))
    assert response.status_code == 200
    assert response.context["seeded"] == ["money"]
    assert response.context["form"].initial["tags"] == ""
    response = admin_client.post(tags_url("pricing.vat_rate"), {"tags": "review, q3"})
    assert response.status_code == 302
    assert sorted(Tag.objects.filter(from_catalogue=False).values_list("name", flat=True)) == ["q3", "review"]
    assigned = TunableDefinitionTag.objects.filter(definition__key="pricing.vat_rate").select_related("tag")
    assert {r.tag.name: r.seeded for r in assigned} == {"money": True, "q3": False, "review": False}
    response = admin_client.get(tags_url("pricing.vat_rate"))
    assert response.context["form"].initial["tags"] == "q3, review"
    response = admin_client.post(tags_url("pricing.vat_rate"), {"tags": "Bad Name"})
    assert response.status_code == 200
    assert "tags" in response.context["form"].errors
    assert admin_client.get(tags_url("weights.nope")).status_code == 404
    assert ChangeSet.objects.count() == 0
    viewer = staff("tunables.view_tunabledefinition")
    assert viewer.get(tags_url("pricing.vat_rate")).status_code == 403
    with restricted():
        assert admin_client.get(tags_url("weights.beta")).status_code == 403
        assert admin_client.post(tags_url("weights.beta"), {"tags": "x"}).status_code == 403


def test_tag_admin(admin_client: Client, synced: SyncResult) -> None:
    from tunables.models import Tag

    tags_admin = "/admin/tunables/tag/"
    response = admin_client.get(tags_admin)
    assert response.status_code == 200
    content = response.content.decode()
    assert "money" in content and "comfort" in content
    response = admin_client.post(
        f"{tags_admin}add/",
        {"name": "review", "description": "Second look", "created_at_0": "2026-09-01", "created_at_1": "00:00:00"},
    )
    assert response.status_code == 302, response.content.decode()[:500]
    review = Tag.objects.get(name="review")
    assert (review.description, review.from_catalogue) == ("Second look", False)
    change = admin_client.get(f"{tags_admin}{review.pk}/change/")
    assert 'name="from_catalogue"' not in change.content.decode()
    assert admin_client.post(f"{tags_admin}{review.pk}/delete/", {"post": "yes"}).status_code == 302
    assert not Tag.objects.filter(name="review").exists()
    money = Tag.objects.get(name="money")
    response = admin_client.get(f"{tags_admin}{money.pk}/delete/")
    assert response.status_code == 403
    admin_client.post(f"{tags_admin}{money.pk}/delete/", {"post": "yes"})
    assert Tag.objects.filter(name="money").exists()
    assert "action-select" not in admin_client.get(tags_admin).content.decode()


def test_seeded_tag_name_is_read_only_in_the_tag_admin(admin_client: Client, synced: SyncResult) -> None:
    from tunables.models import Tag

    money = Tag.objects.get(name="money")
    change = f"/admin/tunables/tag/{money.pk}/change/"
    content = admin_client.get(change).content.decode()
    assert 'name="name"' not in content
    response = admin_client.post(change, {"name": "cash", "description": "Renamed?"})
    assert response.status_code == 302
    money.refresh_from_db()
    assert (money.name, money.description) == ("money", "Renamed?")
    manual = Tag.objects.create(name="review")
    change = f"/admin/tunables/tag/{manual.pk}/change/"
    assert 'name="name"' in admin_client.get(change).content.decode()
    assert admin_client.post(change, {"name": "reviewed", "description": ""}).status_code == 302
    manual.refresh_from_db()
    assert manual.name == "reviewed"
