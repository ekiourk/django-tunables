import json
import re
from collections.abc import Callable
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.template import Context, Template, TemplateDoesNotExist
from django.test import Client, RequestFactory, override_settings

from tests.conftest import user_with
from tests.test_admin import messages_of
from tests.test_services import apply
from tunables import Change
from tunables.errors import UnknownKey
from tunables.models import ChangeSet, Snapshot, State, TunableValue
from tunables.panel import PANEL_TEMPLATE, PanelConfig
from tunables.services import current_version
from tunables.sync import SyncResult

pytestmark = pytest.mark.django_db

SAVE = "/tunables/panel/save/"
PAGE = "/shop/settings/?tab=prices"
KEYS = ("pricing.vat_rate", "thermostat.mode")
# The effective values of the test catalogue at version 0, as a browser would post them unchanged.
UNCHANGED = {"pricing.vat_rate": "0.24", "thermostat.mode": "auto"}
OUT_OF_SYNC = "The catalogue in code differs from the database. Run tunables_sync first."


def writer() -> User:
    return user_with("tunables.add_changeset", username="writer")


def viewer() -> User:
    return user_with("tunables.view_tunabledefinition", username="viewer")


def client_for(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def render_tag(user: Any, tag: str) -> str:
    request = RequestFactory().get(PAGE)
    request.user = user
    return Template("{% load tunables %}" + tag).render(Context({"request": request}))


def token_in(html: str) -> PanelConfig:
    """The panel configuration the rendered form would post back."""
    match = re.search(r'name="panel" value="([^"]+)"', html)
    assert match is not None, "no panel token in the form"
    return PanelConfig.from_token(match.group(1))


def token(keys: tuple[str, ...] = KEYS, reason: str = "required", template: str = PANEL_TEMPLATE) -> str:
    return PanelConfig(keys, reason, template=template).token()  # type: ignore[arg-type]


# A host's own copy of the panel template, served from memory ahead of the package's templates.
CUSTOM_TEMPLATE = "shop/panel.html"
CUSTOM_MARKUP = (
    '<section id="{{ panel.config.id }}" data-host="shop">'
    '<input type="hidden" name="panel" value="{{ panel.token }}">'
    "{% if panel.saved_version %}saved {{ panel.saved_version }}{% endif %}"
    "{{ panel.form.errors }}</section>"
)
EXTENDING_TEMPLATE = "shop/panel_extends.html"
EXTENDING_MARKUP = (
    '{% extends "tunables/panel.html" %}'
    '{% block form_attrs %} hx-post="{{ panel.action_url }}" hx-swap="outerHTML"{% endblock %}'
)
custom_template_installed = override_settings(
    TEMPLATES=[
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "OPTIONS": {
                "context_processors": ["django.template.context_processors.request"],
                "loaders": [
                    (
                        "django.template.loaders.locmem.Loader",
                        {CUSTOM_TEMPLATE: CUSTOM_MARKUP, EXTENDING_TEMPLATE: EXTENDING_MARKUP},
                    ),
                    "django.template.loaders.app_directories.Loader",
                ],
            },
        }
    ]
)


def post_data(keys: tuple[str, ...] = KEYS, reason_mode: str = "required", **overrides: Any) -> dict[str, Any]:
    """A POST for a panel over ``keys`` that resubmits the current values, with ``overrides`` applied."""
    data: dict[str, Any] = {
        "panel": token(keys, reason_mode),
        "next": PAGE,
        "reason": "because",
        "expected_version": current_version() if State.objects.exists() else 0,
        **{key: UNCHANGED[key] for key in keys if key in UNCHANGED},
    }
    data.update(overrides)
    return data


def go_stale_without(key: str) -> None:
    """Make the stored state look like an older catalogue that never had ``key``."""
    group, _, name = key.partition(".")
    document = Snapshot.objects.get(version=current_version()).document
    del document["groups"][group][name]
    with connection.cursor() as cursor:  # history rows are append-only through the ORM
        table = Snapshot._meta.db_table
        cursor.execute(
            f"UPDATE {table} SET document = %s WHERE version = %s", [json.dumps(document), document["version"]]
        )
    State.objects.update(catalogue_version="sha256:older")


def only_pricing(request: Any) -> set[str]:
    return {"pricing"}


def restricted() -> Any:
    return override_settings(
        TUNABLES={"CATALOGUE": "tests.catalogue.catalogue", "EDITABLE_GROUPS": f"{__name__}.only_pricing"}
    )


# Rendering: mode


def test_writer_gets_an_edit_form_posting_to_the_save_view(synced: SyncResult) -> None:
    html = render_tag(writer(), '{% tunables_panel "pricing.vat_rate" "thermostat.mode" %}')
    assert '<section class="tunables-panel tunables-panel--edit" id="tunables-panel">' in html
    assert f'<form method="post" action="{SAVE}"' in html
    assert 'name="csrfmiddlewaretoken"' in html


def test_viewer_gets_read_only_values(synced: SyncResult) -> None:
    html = render_tag(viewer(), '{% tunables_panel "pricing.free_shipping_over" "pricing.currencies" %}')
    assert "tunables-panel--read" in html
    assert "<form" not in html
    assert '<dt class="tunables-panel__key">Free shipping over</dt>' in html
    assert '<dd class="tunables-panel__value">50.0 EUR</dd>' in html
    assert '<dd class="tunables-panel__value">[&quot;EUR&quot;]</dd>' in html


def test_read_only_values_mark_overrides(synced: SyncResult) -> None:
    apply(Change("pricing.free_shipping_over", 75.0))
    html = render_tag(viewer(), '{% tunables_panel "pricing.free_shipping_over" %}')
    assert (
        '<dd class="tunables-panel__value">75.0 EUR <span class="tunables-panel__overridden">overridden</span></dd>'
        in html
    )


def test_a_user_without_permissions_sees_nothing(synced: SyncResult) -> None:
    assert render_tag(user_with(username="nobody"), '{% tunables_panel "pricing.vat_rate" %}').strip() == ""


def test_an_anonymous_user_sees_nothing(synced: SyncResult) -> None:
    assert render_tag(AnonymousUser(), '{% tunables_panel "pricing.vat_rate" %}').strip() == ""


def test_a_never_synced_database_renders_defaults_read_only_with_a_notice(db: None) -> None:
    html = render_tag(writer(), '{% tunables_panel "pricing.vat_rate" %}')
    assert "tunables-panel--read" in html
    assert OUT_OF_SYNC in html
    assert '<dd class="tunables-panel__value">0.24</dd>' in html


def test_a_stale_state_renders_what_the_running_code_knows(synced: SyncResult) -> None:
    apply(Change("thermostat.mode", "heat"))
    go_stale_without("pricing.vat_rate")
    html = render_tag(writer(), '{% tunables_panel "pricing.vat_rate" "thermostat.mode" %}')
    assert "tunables-panel--read" in html and OUT_OF_SYNC in html
    assert '<dd class="tunables-panel__value">0.24</dd>' in html
    assert '<dd class="tunables-panel__value">&quot;heat&quot; <span class="tunables-panel__overridden">' in html


def test_a_group_outside_the_editable_set_turns_the_whole_panel_read_only(synced: SyncResult) -> None:
    with restricted():
        assert "tunables-panel--edit" in render_tag(writer(), '{% tunables_panel "pricing.vat_rate" %}')
        assert "tunables-panel--read" in render_tag(
            writer(), '{% tunables_panel "pricing.vat_rate" "thermostat.mode" %}'
        )


# Rendering: the form


def test_the_form_carries_the_panel_configuration_as_a_signed_token(synced: SyncResult) -> None:
    html = render_tag(writer(), '{% tunables_panel "pricing.vat_rate" "thermostat.mode" title="Prices" id="p" %}')
    assert token_in(html) == PanelConfig(KEYS, "required", "Prices", "p")


def test_the_form_returns_to_the_current_page_by_default(synced: SyncResult) -> None:
    html = render_tag(writer(), '{% tunables_panel "pricing.vat_rate" %}')
    assert f'<input type="hidden" name="next" value="{PAGE}">' in html


def test_the_next_option_sets_the_return_url(synced: SyncResult) -> None:
    html = render_tag(writer(), '{% tunables_panel "pricing.vat_rate" next="/other/" %}')
    assert '<input type="hidden" name="next" value="/other/">' in html


def test_the_form_names_fields_by_full_key_and_offers_a_reset_per_override(synced: SyncResult) -> None:
    apply(Change("pricing.vat_rate", 0.2))
    html = render_tag(writer(), '{% tunables_panel "pricing.vat_rate" "thermostat.mode" %}')
    assert 'name="expected_version" value="1"' in html
    assert 'name="pricing.vat_rate"' in html and 'value="0.2"' in html
    assert 'name="reset_pricing.vat_rate"' in html
    assert 'name="thermostat.mode"' in html and 'name="reset_thermostat.mode"' not in html


def test_title_and_id_options_render(synced: SyncResult) -> None:
    html = render_tag(writer(), '{% tunables_panel "pricing.vat_rate" title="Prices" id="prices" %}')
    assert 'id="prices"' in html
    assert '<h3 class="tunables-panel__title">Prices</h3>' in html


def test_a_required_reason_renders_as_a_textarea(synced: SyncResult) -> None:
    html = render_tag(writer(), '{% tunables_panel "pricing.vat_rate" %}')
    assert '<textarea name="reason"' in html


def test_a_hidden_reason_renders_as_a_hidden_input(synced: SyncResult) -> None:
    html = render_tag(writer(), '{% tunables_panel "pricing.vat_rate" reason="hidden" %}')
    assert '<input type="hidden" name="reason"' in html
    assert "<textarea" not in html


def test_the_template_option_selects_the_template(synced: SyncResult) -> None:
    with pytest.raises(TemplateDoesNotExist, match="missing.html"):
        render_tag(writer(), '{% tunables_panel "pricing.vat_rate" template="missing.html" %}')


@custom_template_installed
def test_the_template_option_travels_in_the_token(synced: SyncResult) -> None:
    html = render_tag(writer(), '{% tunables_panel "pricing.vat_rate" template="shop/panel.html" %}')
    assert token_in(html) == PanelConfig(("pricing.vat_rate",), template=CUSTOM_TEMPLATE)


@custom_template_installed
def test_a_template_extending_the_panel_adds_form_attributes_through_the_block(synced: SyncResult) -> None:
    html = render_tag(writer(), '{% tunables_panel "pricing.vat_rate" template="shop/panel_extends.html" %}')
    assert 'class="tunables-panel__form" hx-post="/tunables/panel/save/" hx-swap="outerHTML">' in html


def test_an_unknown_key_is_refused_even_for_a_user_who_would_see_nothing(synced: SyncResult) -> None:
    with pytest.raises(UnknownKey):
        render_tag(AnonymousUser(), '{% tunables_panel "pricing.nope" %}')


def test_an_unknown_reason_mode_is_refused(synced: SyncResult) -> None:
    with pytest.raises(ValueError, match="reason must be one of"):
        render_tag(writer(), '{% tunables_panel "pricing.vat_rate" reason="maybe" %}')


def test_the_panel_needs_the_request_in_the_context(synced: SyncResult) -> None:
    with pytest.raises(ImproperlyConfigured, match="context_processors.request"):
        Template('{% load tunables %}{% tunables_panel "pricing.vat_rate" %}').render(Context({}))


def test_tunable_value_prints_the_effective_value(synced: SyncResult) -> None:
    apply(Change("thermostat.mode", "heat"))
    template = Template('{% load tunables %}{% tunable_value "thermostat.mode" %}')
    assert template.render(Context({})) == "heat"


def test_tunable_value_can_be_stored_in_a_variable(synced: SyncResult) -> None:
    template = Template('{% load tunables %}{% tunable_value "pricing.vat_rate" as rate %}{{ rate }}')
    assert template.render(Context({})) == "0.24"


# Saving: success


def test_a_save_redirects_to_the_page_with_a_message(synced: SyncResult) -> None:
    response = client_for(writer()).post(SAVE, post_data(**{"thermostat.mode": "heat"}))
    assert response.status_code == 302 and response["Location"] == PAGE
    assert messages_of(response) == ["Saved version 1."]


def test_a_save_writes_one_change_set_from_the_panel(synced: SyncResult) -> None:
    client_for(writer()).post(SAVE, post_data(**{"pricing.vat_rate": "0.2", "thermostat.mode": "heat"}))
    changeset = ChangeSet.objects.get(version=1)
    assert changeset.source == "panel" and changeset.actor == "writer" and changeset.reason == "because"
    assert {item.key: item.new_value for item in changeset.items.all()} == {
        "pricing.vat_rate": 0.2,
        "thermostat.mode": "heat",
    }


def test_a_save_records_the_page_path_in_the_metadata(synced: SyncResult) -> None:
    client_for(writer()).post(SAVE, post_data(**{"thermostat.mode": "heat"}))
    assert ChangeSet.objects.get(version=1).metadata == {"page": "/shop/settings/"}


def test_the_reset_box_removes_the_override(synced: SyncResult) -> None:
    apply(Change("pricing.vat_rate", 0.2))
    data = post_data(**{"pricing.vat_rate": "0.2", "reset_pricing.vat_rate": "on"})
    assert client_for(writer()).post(SAVE, data).status_code == 302
    assert not TunableValue.objects.filter(key="pricing.vat_rate").exists()


def test_a_hidden_reason_is_stored_as_the_page(synced: SyncResult) -> None:
    client_for(writer()).post(SAVE, post_data(reason_mode="hidden", reason="", **{"thermostat.mode": "heat"}))
    assert ChangeSet.objects.get(version=1).reason == "Changed from /shop/settings/"


def test_an_optional_reason_left_empty_is_stored_as_the_page(synced: SyncResult) -> None:
    client_for(writer()).post(SAVE, post_data(reason_mode="optional", reason="", **{"thermostat.mode": "heat"}))
    assert ChangeSet.objects.get(version=1).reason == "Changed from /shop/settings/"


def test_an_optional_reason_that_was_typed_is_kept(synced: SyncResult) -> None:
    client_for(writer()).post(SAVE, post_data(reason_mode="optional", reason="typed", **{"thermostat.mode": "heat"}))
    assert ChangeSet.objects.get(version=1).reason == "typed"


# Saving: failures


def test_a_required_reason_left_empty_shows_the_error_page(synced: SyncResult) -> None:
    response = client_for(writer()).post(SAVE, post_data(reason="", **{"thermostat.mode": "heat"}))
    assert response.status_code == 200
    html = response.content.decode()
    assert html.startswith("<!doctype html>")
    assert f'<a href="{PAGE}">Back</a>' in html
    assert "This field is required." in html
    assert ChangeSet.objects.count() == 0


def test_an_invalid_value_is_reported_on_its_field_and_the_rest_is_kept(synced: SyncResult) -> None:
    response = client_for(writer()).post(SAVE, post_data(**{"pricing.vat_rate": "5"}))
    form = response.context["panel"].form
    assert list(form.errors) == ["pricing.vat_rate"]
    assert "must be <= 1.0" in form.errors["pricing.vat_rate"]
    assert form["thermostat.mode"].value() == "auto"


def test_resubmitting_the_current_values_is_reported_as_nothing_changed(synced: SyncResult) -> None:
    response = client_for(writer()).post(SAVE, post_data())
    assert response.context["panel"].form.non_field_errors() == ["Nothing changed."]
    assert ChangeSet.objects.count() == 0


def test_group_rules_apply_to_a_partial_edit(synced: SyncResult) -> None:
    data = post_data(keys=("weights.alpha",), **{"weights.alpha": "0.9"})
    response = client_for(writer()).post(SAVE, data)
    assert response.context["panel"].form.non_field_errors() == ["weights must sum to 1"]


def test_a_stale_version_keeps_the_input_and_bumps_the_version(synced: SyncResult) -> None:
    data = post_data(**{"pricing.vat_rate": "0.1"})
    apply(Change("pricing.vat_rate", 0.2))
    response = client_for(writer()).post(SAVE, data)
    assert response.status_code == 200
    panel = response.context["panel"]
    assert "version 0 is now version 1" in panel.notice and "pricing.vat_rate" in panel.notice
    assert panel.form["pricing.vat_rate"].value() == "0.1"
    assert panel.form["expected_version"].value() == "1"
    assert ChangeSet.objects.count() == 1


def test_an_unsafe_next_falls_back_to_the_referer(synced: SyncResult) -> None:
    data = post_data(next="https://evil.example/x", **{"thermostat.mode": "heat"})
    response = client_for(writer()).post(SAVE, data, HTTP_REFERER="/from/")
    assert response["Location"] == "/from/"
    assert ChangeSet.objects.get(version=1).metadata == {"page": "/from/"}


def test_an_unsafe_next_without_a_referer_falls_back_to_the_root(synced: SyncResult) -> None:
    response = client_for(writer()).post(SAVE, post_data(next="//evil.example/x", **{"thermostat.mode": "heat"}))
    assert response["Location"] == "/"


# Saving: partial responses


def test_a_partial_save_returns_the_fresh_panel_with_the_saved_version(synced: SyncResult) -> None:
    response = client_for(writer()).post(SAVE, post_data(**{"thermostat.mode": "heat"}), HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    html = response.content.decode()
    assert "<!doctype" not in html and "tunables-panel--edit" in html
    assert "Saved version 1." in html
    assert 'name="expected_version" value="1"' in html
    assert "selected>heat" in html
    assert messages_of(response) == []


def test_a_partial_failure_returns_the_panel_with_its_errors(synced: SyncResult) -> None:
    response = client_for(writer()).post(SAVE, post_data(partial="1", **{"pricing.vat_rate": "5"}))
    assert "<!doctype" not in response.content.decode()
    assert "pricing.vat_rate" in response.context["panel"].form.errors


@custom_template_installed
def test_a_partial_save_renders_the_template_the_tag_was_given(synced: SyncResult) -> None:
    data = post_data(panel=token(template=CUSTOM_TEMPLATE), **{"thermostat.mode": "heat"})
    html = client_for(writer()).post(SAVE, data, HTTP_HX_REQUEST="true").content.decode()
    assert 'data-host="shop"' in html and "saved 1" in html


@custom_template_installed
def test_the_error_page_renders_the_template_the_tag_was_given(synced: SyncResult) -> None:
    data = post_data(panel=token(template=CUSTOM_TEMPLATE), **{"pricing.vat_rate": "5"})
    html = client_for(writer()).post(SAVE, data).content.decode()
    assert "<!doctype" in html and 'data-host="shop"' in html


# Saving: refusals


def test_an_unsynced_catalogue_redirects_with_an_error(db: None) -> None:
    response = client_for(writer()).post(SAVE, post_data(expected_version=0, **{"thermostat.mode": "heat"}))
    assert response.status_code == 302 and response["Location"] == PAGE
    assert messages_of(response) == [OUT_OF_SYNC]
    assert ChangeSet.objects.count() == 0


def test_an_unsynced_catalogue_answers_a_partial_request_with_the_read_only_panel(db: None) -> None:
    response = client_for(writer()).post(SAVE, post_data(expected_version=0, partial="1"))
    assert response.status_code == 200 and "tunables-panel--read" in response.content.decode()


def test_a_stale_state_refuses_the_save_without_crashing(synced: SyncResult) -> None:
    data = post_data(**{"pricing.vat_rate": "0.2"})
    go_stale_without("pricing.vat_rate")
    response = client_for(writer()).post(SAVE, {**data, "partial": "1"})
    assert response.status_code == 200 and "tunables-panel--read" in response.content.decode()
    assert ChangeSet.objects.count() == 0


def test_an_anonymous_save_is_forbidden(synced: SyncResult) -> None:
    assert Client().post(SAVE, post_data(**{"thermostat.mode": "heat"})).status_code == 403


def test_an_anonymous_save_is_forbidden_before_the_token_is_read(synced: SyncResult) -> None:
    assert Client().post(SAVE, {**post_data(), "panel": "garbage"}).status_code == 403


def test_a_viewer_may_not_save(synced: SyncResult) -> None:
    assert client_for(viewer()).post(SAVE, post_data(**{"thermostat.mode": "heat"})).status_code == 403


def test_a_key_outside_the_editable_groups_is_forbidden(synced: SyncResult) -> None:
    with restricted():
        assert client_for(writer()).post(SAVE, post_data(**{"thermostat.mode": "heat"})).status_code == 403
    assert ChangeSet.objects.count() == 0


def test_keys_inside_the_editable_groups_are_saved(synced: SyncResult) -> None:
    with restricted():
        data = post_data(keys=("pricing.vat_rate",), **{"pricing.vat_rate": "0.2"})
        assert client_for(writer()).post(SAVE, data).status_code == 302
    assert ChangeSet.objects.count() == 1


def forged_signature() -> str:
    """A token whose payload says the reason is hidden but whose signature belongs to a required-reason panel."""
    required, hidden = token(KEYS, "required"), token(KEYS, "hidden")
    return hidden[: hidden.rindex(":")] + required[required.rindex(":") :]


@pytest.mark.parametrize(
    "make_token", [lambda: "", lambda: "garbage", forged_signature], ids=["missing", "garbage", "forged"]
)
def test_a_missing_or_altered_token_is_a_bad_request(synced: SyncResult, make_token: Callable[[], str]) -> None:
    data = post_data(reason="", **{"thermostat.mode": "heat"})
    assert client_for(writer()).post(SAVE, {**data, "panel": make_token()}).status_code == 400
    assert ChangeSet.objects.count() == 0


def test_a_panel_over_an_unknown_key_is_not_found(synced: SyncResult) -> None:
    assert client_for(writer()).post(SAVE, post_data(keys=("pricing.nope",))).status_code == 404


def test_a_panel_over_no_key_is_a_bad_request(synced: SyncResult) -> None:
    assert client_for(writer()).post(SAVE, post_data(keys=())).status_code == 400


def test_the_save_view_only_accepts_post(synced: SyncResult) -> None:
    assert client_for(writer()).get(SAVE).status_code == 405
