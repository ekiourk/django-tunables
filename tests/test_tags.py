import contextlib
import logging
from typing import Any

import pytest
from django.test import override_settings

from tunables import tags
from tunables.errors import TagExists, TagSeeded
from tunables.models import Tag, TunableDefinition, TunableDefinitionTag
from tunables.sync import SyncResult
from tunables.tags import create_tag, delete_tag, set_manual_tags, update_tag

pytestmark = pytest.mark.django_db


def assignments(key: str) -> dict[str, bool]:
    return {
        r.tag.name: r.seeded for r in TunableDefinitionTag.objects.filter(definition__key=key).select_related("tag")
    }


def test_create_update_delete_manual_tag(synced: SyncResult) -> None:
    tag = tags.create_tag("review", "Needs a second look.")
    assert (tag.name, tag.description, tag.from_catalogue) == ("review", "Needs a second look.", False)
    with pytest.raises(TagExists):
        tags.create_tag("review")
    with pytest.raises(TagExists):
        tags.create_tag("money")
    with pytest.raises(ValueError, match="must match"):
        tags.create_tag("Bad Name")
    assert tags.update_tag("review", "Checked.").description == "Checked."
    with pytest.raises(Tag.DoesNotExist):
        tags.update_tag("nope", "x")
    tags.set_manual_tags("weights.beta", ["review"])
    tags.delete_tag("review")
    assert not Tag.objects.filter(name="review").exists()
    assert assignments("weights.beta") == {}
    with pytest.raises(Tag.DoesNotExist):
        tags.delete_tag("review")


def test_seeded_tag_cannot_be_deleted(synced: SyncResult) -> None:
    with pytest.raises(TagSeeded):
        tags.delete_tag("money")
    assert Tag.objects.filter(name="money").exists()


def test_set_manual_tags_replaces_manual_and_keeps_seeded(synced: SyncResult) -> None:
    assert tags.set_manual_tags("weights.beta", ["review", "q3"]) == ["q3", "review"]
    assert assignments("weights.beta") == {"review": False, "q3": False}
    assert Tag.objects.get(name="q3").from_catalogue is False
    assert tags.set_manual_tags("weights.beta", ["review"]) == ["review"]
    assert assignments("weights.beta") == {"review": False}
    assert Tag.objects.filter(name="q3").exists()
    assert tags.set_manual_tags("pricing.vat_rate", []) == ["money"]
    assert assignments("pricing.vat_rate") == {"money": True}
    assert tags.set_manual_tags("pricing.vat_rate", ["money", "review"]) == ["money", "review"]
    assert assignments("pricing.vat_rate") == {"money": True, "review": False}
    with pytest.raises(ValueError, match="must match"):
        tags.set_manual_tags("weights.beta", ["Bad"])
    with pytest.raises(TunableDefinition.DoesNotExist):
        tags.set_manual_tags("weights.nope", ["review"])


def test_an_assignment_records_who_and_when(synced: SyncResult) -> None:
    from django.utils import timezone

    before = timezone.now()
    set_manual_tags("pricing.vat_rate", ["review"], actor="alice")
    row = TunableDefinitionTag.objects.get(definition__key="pricing.vat_rate", tag__name="review")
    assert row.assigned_by == "alice"
    assert row.assigned_at >= before


def test_re_attaching_a_tag_restamps_it(synced: SyncResult) -> None:
    set_manual_tags("pricing.vat_rate", ["review"], actor="alice")
    first = TunableDefinitionTag.objects.get(tag__name="review").assigned_at
    set_manual_tags("pricing.vat_rate", [], actor="alice")
    set_manual_tags("pricing.vat_rate", ["review"], actor="bob")
    row = TunableDefinitionTag.objects.get(tag__name="review")
    assert row.assigned_by == "bob"
    assert row.assigned_at > first


def test_seeded_assignments_say_system(synced: SyncResult) -> None:
    row = TunableDefinitionTag.objects.filter(seeded=True).first()
    assert row is not None
    assert row.assigned_by == "system"


def test_every_tag_change_is_logged(
    synced: SyncResult, caplog: pytest.LogCaptureFixture, django_capture_on_commit_callbacks: Any
) -> None:
    with caplog.at_level(logging.INFO, logger="tunables.tags"), django_capture_on_commit_callbacks(execute=True):
        create_tag("review", actor="alice")
        set_manual_tags("pricing.vat_rate", ["review"], actor="alice")
        update_tag("review", "Needs a look", actor="bob")
        delete_tag("review", actor="bob")
    messages = [record.getMessage() for record in caplog.records]
    assert messages == [
        "tag 'review' created by alice",
        "tags of 'pricing.vat_rate' set by alice: was [money], now [money, review]",
        "tag 'review' described by bob",
        "tag 'review' deleted by bob",
    ]


def test_an_unattributed_change_still_logs(
    synced: SyncResult, caplog: pytest.LogCaptureFixture, django_capture_on_commit_callbacks: Any
) -> None:
    with caplog.at_level(logging.INFO, logger="tunables.tags"), django_capture_on_commit_callbacks(execute=True):
        create_tag("review")
    assert caplog.records[0].getMessage() == "tag 'review' created by an unnamed caller"
    assert TunableDefinitionTag.objects.filter(tag__name="review").count() == 0


def test_sync_keeps_who_attached_a_tag_it_later_seeds(synced: SyncResult) -> None:
    from dataclasses import replace

    from tests.catalogue import limits, pricing, thermostat, weights
    from tests.test_sync import alt
    from tunables.sync import sync

    set_manual_tags("weights.alpha", ["review"], actor="alice")
    row = TunableDefinitionTag.objects.get(definition__key="weights.alpha", tag__name="review")
    stamped = row.assigned_at

    tunables = [replace(t, tags=["review"]) if t.name == "alpha" else t for t in weights.tunables]
    globals()["seeding"] = alt([pricing, thermostat, replace(weights, tunables=tunables), limits])
    with override_settings(TUNABLES={"CATALOGUE": f"{__name__}.seeding"}):
        sync()
    row.refresh_from_db()
    assert (row.seeded, row.assigned_by) == (True, "alice")
    assert row.assigned_at == stamped


def test_dropping_a_seed_keeps_a_human_assignment(synced: SyncResult) -> None:
    from tunables.sync import sync

    set_manual_tags("weights.alpha", ["review"], actor="alice")
    TunableDefinitionTag.objects.filter(definition__key="weights.alpha", tag__name="review").update(seeded=True)
    sync()
    row = TunableDefinitionTag.objects.filter(definition__key="weights.alpha", tag__name="review").first()
    assert row is not None
    assert (row.seeded, row.assigned_by) == (False, "alice")


def test_a_rolled_back_change_logs_nothing(synced: SyncResult, caplog: pytest.LogCaptureFixture) -> None:
    from django.db import transaction

    logs = caplog.at_level(logging.INFO, logger="tunables.tags")
    with logs, contextlib.suppress(RuntimeError), transaction.atomic():
        create_tag("review", actor="alice")
        set_manual_tags("pricing.vat_rate", ["review"], actor="alice")
        raise RuntimeError("the caller changed its mind")
    assert Tag.objects.filter(name="review").count() == 0
    assert caplog.records == []


def test_a_committed_change_still_logs(
    synced: SyncResult, caplog: pytest.LogCaptureFixture, django_capture_on_commit_callbacks: Any
) -> None:
    with caplog.at_level(logging.INFO, logger="tunables.tags"), django_capture_on_commit_callbacks(execute=True):
        create_tag("review", actor="alice")
    assert [record.getMessage() for record in caplog.records] == ["tag 'review' created by alice"]


def test_a_shell_assignment_survives_a_seed_coming_and_going(synced: SyncResult) -> None:
    from tunables.sync import sync

    set_manual_tags("weights.alpha", ["review"])
    assert TunableDefinitionTag.objects.get(tag__name="review").assigned_by == ""
    TunableDefinitionTag.objects.filter(tag__name="review").update(seeded=True)
    sync()
    row = TunableDefinitionTag.objects.filter(tag__name="review").first()
    assert row is not None
    assert (row.seeded, row.assigned_by) == (False, "")


def test_sync_logs_the_seed_it_drops(
    synced: SyncResult, caplog: pytest.LogCaptureFixture, django_capture_on_commit_callbacks: Any
) -> None:
    from tunables.sync import sync

    set_manual_tags("weights.alpha", ["review"], actor="alice")
    TunableDefinitionTag.objects.filter(tag__name="review").update(seeded=True)
    TunableDefinitionTag.objects.filter(tag__name="comfort", definition__key="thermostat.mode").update(
        assigned_by="system"
    )
    with caplog.at_level(logging.INFO, logger="tunables.tags"), django_capture_on_commit_callbacks(execute=True):
        sync()
    assert [record.getMessage() for record in caplog.records] == [
        "seed tag 'review' of 'weights.alpha' dropped by the catalogue, kept as a manual assignment"
    ]
