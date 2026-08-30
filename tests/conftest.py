import pytest
from django.utils import timezone

from tunables.models import ActorSource, ChangeSet, ChangeSource, TunableDefinition


@pytest.fixture
def definition() -> TunableDefinition:
    return TunableDefinition.objects.create(
        key="pricing.vat_rate",
        group_name="pricing",
        name="vat_rate",
        order=0,
        type_name="float",
        type_params={"min": 0.0, "max": 1.0},
        default=0.24,
        title="VAT rate",
        synced_at=timezone.now(),
    )


@pytest.fixture
def changeset() -> ChangeSet:
    return ChangeSet.objects.create(
        version=1,
        actor="alice",
        actor_source=ActorSource.VERIFIED,
        source=ChangeSource.API,
        catalogue_version="sha256:test",
    )
