import pytest
from django import forms

from tests.catalogue import catalogue
from tests.test_services import apply
from tunables import Actor, Change
from tunables.errors import UnknownKey
from tunables.forms import TunablesForm, build_form, form_class
from tunables.services import latest_snapshot
from tunables.sync import SyncResult

pytestmark = pytest.mark.django_db

KEYS = ["pricing.vat_rate", "thermostat.mode"]
ALICE = Actor("alice", "verified")
# The effective values at version 0, as a browser would post them unchanged.
UNCHANGED = {"reason": "r", "expected_version": "0", "pricing.vat_rate": "0.24", "thermostat.mode": "auto"}


def test_fields_are_named_by_full_key_across_groups(synced: SyncResult) -> None:
    form = build_form(KEYS, latest_snapshot().document)()
    assert list(form.fields) == ["reason", "expected_version", "pricing.vat_rate", "thermostat.mode"]
    assert form.keys == ("pricing.vat_rate", "thermostat.mode")


def test_fields_start_at_the_effective_values(synced: SyncResult) -> None:
    apply(Change("pricing.vat_rate", 0.2))
    form = build_form(KEYS, latest_snapshot().document)()
    assert form.fields["pricing.vat_rate"].initial == 0.2
    assert form.fields["thermostat.mode"].initial == "auto"
    assert form.fields["expected_version"].initial == 1


def test_an_overridden_key_gets_a_reset_box(synced: SyncResult) -> None:
    apply(Change("pricing.vat_rate", 0.2))
    form = build_form(KEYS, latest_snapshot().document)()
    assert "reset_pricing.vat_rate" in form.fields
    assert "reset_thermostat.mode" not in form.fields


def test_duplicate_keys_are_kept_once_in_first_position(synced: SyncResult) -> None:
    form = build_form(["thermostat.mode", "pricing.vat_rate", "thermostat.mode"], latest_snapshot().document)
    assert form.keys == ("thermostat.mode", "pricing.vat_rate")


def test_an_unknown_key_is_refused(synced: SyncResult) -> None:
    with pytest.raises(UnknownKey):
        build_form(["pricing.nope"], latest_snapshot().document)


def test_the_base_form_requires_a_reason() -> None:
    assert TunablesForm().fields["reason"].required is True


def test_a_required_reason_is_a_mandatory_textarea(synced: SyncResult) -> None:
    field = build_form(KEYS, latest_snapshot().document)().fields["reason"]
    assert field.required is True and isinstance(field.widget, forms.Textarea)


def test_an_optional_reason_is_a_textarea_that_may_stay_empty(synced: SyncResult) -> None:
    field = build_form(KEYS, latest_snapshot().document, reason="optional")().fields["reason"]
    assert field.required is False and isinstance(field.widget, forms.Textarea)


def test_a_hidden_reason_is_a_hidden_input(synced: SyncResult) -> None:
    field = build_form(KEYS, latest_snapshot().document, reason="hidden")().fields["reason"]
    assert field.required is False and isinstance(field.widget, forms.HiddenInput)


def test_an_unknown_reason_mode_is_refused(synced: SyncResult) -> None:
    with pytest.raises(ValueError, match="reason must be one of"):
        build_form(KEYS, latest_snapshot().document, reason="maybe")  # type: ignore[arg-type]


def test_changes_cover_every_key_with_a_reset_where_ticked(synced: SyncResult) -> None:
    apply(Change("pricing.vat_rate", 0.2))
    data = {**UNCHANGED, "expected_version": "1", "reset_pricing.vat_rate": "on", "thermostat.mode": "heat"}
    form = build_form(KEYS, latest_snapshot().document)(data)
    assert form.is_valid(), form.errors
    assert form.changes() == [Change("pricing.vat_rate", reset=True), Change("thermostat.mode", "heat")]


def test_an_emptied_value_without_reset_is_an_error(synced: SyncResult) -> None:
    form = build_form(KEYS, latest_snapshot().document)({**UNCHANGED, "pricing.vat_rate": ""})
    assert not form.is_valid()
    assert form.errors == {"pricing.vat_rate": ["Enter a value or tick reset to default."]}


def test_rows_carry_fields_values_and_reset_boxes(synced: SyncResult) -> None:
    apply(Change("pricing.free_shipping_over", 75.0))
    form = build_form(["pricing.free_shipping_over", "pricing.currencies"], latest_snapshot().document)()
    shipping, currencies = form.rows()
    assert shipping.tunable is catalogue.get("pricing.free_shipping_over")
    assert shipping.field.name == "pricing.free_shipping_over"
    assert shipping.shown == "75.0 EUR"
    assert shipping.reset is not None and shipping.reset.name == "reset_pricing.free_shipping_over"
    assert currencies.shown == '["EUR"]' and currencies.reset is None


def test_form_class_works_without_the_registered_catalogue() -> None:
    from tunables import Group, String, Tunable

    tunable = Tunable("greeting", String(), "hi", title="Greeting")
    Group("shop", [tunable])
    form = form_class({"shop.greeting": tunable}, {"shop.greeting": "hello"}, ["shop.greeting"], 3)()
    assert form.fields["shop.greeting"].initial == "hello"
    assert str(form.fields["reset_shop.greeting"].label) == 'Reset to default ("hi")'
    assert form.fields["expected_version"].initial == 3


# save()


def valid_form(**data: str) -> TunablesForm:
    form = build_form(KEYS, latest_snapshot().document)({**UNCHANGED, **data})
    assert form.is_valid(), form.errors
    return form


def test_save_returns_the_result_of_a_written_change_set(synced: SyncResult) -> None:
    result = valid_form(**{"thermostat.mode": "heat"}).save(actor=ALICE, source="api", reason="warm up")
    assert result is not None and result.version == 1
    assert result.changeset.reason == "warm up" and result.changeset.actor == "alice"


def test_save_reports_nothing_changed_on_the_form(synced: SyncResult) -> None:
    form = valid_form()
    assert form.save(actor=ALICE, source="api") is None
    assert form.non_field_errors() == ["Nothing changed."]


def test_save_reports_a_group_rule_violation_on_the_form(synced: SyncResult) -> None:
    form = build_form(["weights.alpha"], latest_snapshot().document)({**UNCHANGED, "weights.alpha": "0.9"})
    assert form.is_valid(), form.errors
    assert form.save(actor=ALICE, source="api") is None
    assert form.non_field_errors() == ["weights must sum to 1"]


def test_save_keeps_a_version_conflict_on_the_form(synced: SyncResult) -> None:
    form = valid_form(**{"thermostat.mode": "heat"})
    apply(Change("pricing.vat_rate", 0.2))
    assert form.save(actor=ALICE, source="api") is None
    assert form.conflict is not None and (form.conflict.expected, form.conflict.actual) == (0, 1)
    assert form.errors == {}


def test_conflict_text_names_the_keys_of_the_form_that_moved(synced: SyncResult) -> None:
    form = valid_form(**{"thermostat.mode": "heat"})
    apply(Change("pricing.vat_rate", 0.2), Change("weights.alpha", 0.5))
    form.save(actor=ALICE, source="api")
    text = form.conflict_text()
    assert "version 0 is now version 1" in text
    assert "pricing.vat_rate" in text and "weights.alpha" not in text


def test_rebound_keeps_the_input_against_the_current_version(synced: SyncResult) -> None:
    form = valid_form(**{"thermostat.mode": "heat"})
    apply(Change("pricing.vat_rate", 0.2))
    form.save(actor=ALICE, source="api")
    rebound = form.rebound()
    assert rebound["expected_version"].value() == "1"
    assert rebound["thermostat.mode"].value() == "heat"
    assert rebound.conflict is form.conflict
    assert rebound.is_valid() and rebound.save(actor=ALICE, source="api") is not None
