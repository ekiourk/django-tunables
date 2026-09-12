import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from tunables.models import ActorSource, ChangeSet, ChangeSource, TunableDefinition
from tunables.sync import SyncResult, sync


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


@pytest.fixture
def synced(db: None) -> SyncResult:
    return sync()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--postgres", action="store_true", help="Run the whole suite on a PostgreSQL testcontainer.")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--postgres"):
        return
    skip = pytest.mark.skip(reason="needs --postgres")
    for item in items:
        if "postgres" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def django_db_modify_db_settings(
    request: pytest.FixtureRequest, django_db_modify_db_settings_parallel_suffix: None
) -> None:
    if not request.config.getoption("--postgres"):
        return
    from django.conf import settings
    from django.db import connections
    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer("postgres:17", driver=None)
    container.start()
    request.addfinalizer(container.stop)
    settings.DATABASES["default"] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": container.dbname,
        "USER": container.username,
        "PASSWORD": container.password,
        "HOST": container.get_container_host_ip(),
        "PORT": container.get_exposed_port(container.port),
    }
    # The handler has already read DATABASES; drop its cached settings and any open connection.
    connections.close_all()
    connections.__dict__.pop("settings", None)
    connections._settings = None
    if hasattr(connections._connections, "default"):
        del connections["default"]


@pytest.fixture
def api(synced: SyncResult) -> APIClient:
    return APIClient()
