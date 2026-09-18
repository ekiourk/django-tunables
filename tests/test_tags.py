import pytest

from tunables import tags
from tunables.errors import TagExists, TagSeeded
from tunables.models import Tag, TunableDefinition, TunableDefinitionTag
from tunables.sync import SyncResult

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
