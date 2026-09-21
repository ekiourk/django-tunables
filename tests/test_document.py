import json
from collections.abc import Callable
from datetime import UTC, datetime
from importlib import resources
from typing import Any

import pytest
from django.test import override_settings
from jsonschema import Draft202012Validator, ValidationError

from tests.catalogue import catalogue
from tunables.document import FORMAT_VERSION, build_document

CREATED_AT = datetime(2026, 9, 5, 9, 53, 19, 123456, tzinfo=UTC)

OVERRIDES = {
    "thermostat.mode": "eco",
    "pricing.vat_rate": 0.2,
    "pricing.currencies": ["USD", "GBP"],
}


def defaults_document() -> dict[str, Any]:
    return build_document(catalogue, {}, version=0, created_at=CREATED_AT)


def overrides_document() -> dict[str, Any]:
    return build_document(catalogue, OVERRIDES, version=42, created_at=CREATED_AT, environment="production")


def test_defaults_only() -> None:
    document = defaults_document()
    assert document["groups"] == catalogue.defaults()
    assert document["overridden"] == []


def test_overrides_replace_defaults_and_are_listed_sorted() -> None:
    document = overrides_document()
    assert document["groups"]["pricing"]["vat_rate"] == 0.2
    assert document["groups"]["pricing"]["currencies"] == ["USD", "GBP"]
    assert document["groups"]["thermostat"]["mode"] == "eco"
    assert document["groups"]["pricing"]["free_shipping_over"] == 50.0
    assert document["groups"]["weights"] == {"alpha": 0.5, "beta": 0.3, "gamma": 0.2}
    assert document["overridden"] == ["pricing.currencies", "pricing.vat_rate", "thermostat.mode"]


def test_metadata_fields() -> None:
    document = overrides_document()
    assert document["format_version"] == FORMAT_VERSION == 1
    assert document["version"] == 42
    assert document["created_at"] == "2026-09-05T09:53:19.123456+00:00"
    assert document["catalogue_version"] == catalogue.version
    assert document["environment"] == "production"


def test_environment_defaults_to_empty_string() -> None:
    assert defaults_document()["environment"] == ""


def test_unknown_override_key_is_ignored() -> None:
    document = build_document(catalogue, {"pricing.discount": 5, "gone.key": 1}, version=1, created_at=CREATED_AT)
    assert "discount" not in document["groups"]["pricing"]
    assert "gone" not in document["groups"]
    assert document["overridden"] == []


def test_deprecated_tunable_is_present() -> None:
    assert defaults_document()["groups"]["thermostat"]["legacy_offset"] == 0.0


def test_groups_follow_catalogue_order() -> None:
    document = defaults_document()
    assert list(document["groups"]) == list(catalogue.groups)
    assert list(document["groups"]["thermostat"]) == [t.name for t in catalogue.groups["thermostat"].tunables]


def test_naive_created_at_is_rejected() -> None:
    with pytest.raises(ValueError, match="aware"):
        build_document(catalogue, {}, version=0, created_at=datetime(2026, 9, 5))


def test_document_round_trips_through_json() -> None:
    document = overrides_document()
    assert json.loads(json.dumps(document)) == document


def schema() -> dict[str, Any]:
    text = resources.files("tunables").joinpath("schemas/snapshot-v1.schema.json").read_text()
    return dict(json.loads(text))


def test_shipped_schema_is_a_valid_2020_12_schema() -> None:
    Draft202012Validator.check_schema(schema())
    assert schema()["$schema"] == "https://json-schema.org/draft/2020-12/schema"


@pytest.mark.parametrize("build", [defaults_document, overrides_document])
def test_document_validates_against_shipped_schema(build: Any) -> None:
    Draft202012Validator(schema(), format_checker=Draft202012Validator.FORMAT_CHECKER).validate(build())


def without(key: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return lambda document: {k: v for k, v in document.items() if k != key}


def replace(**changes: Any) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return lambda document: {**document, **changes}


@pytest.mark.parametrize(
    "break_document",
    [
        without("overridden"),
        without("groups"),
        replace(format_version=2),
        replace(version=-1),
        replace(catalogue_version="abc"),
        replace(created_at="yesterday"),
        replace(overridden=["a", "a"]),
        replace(groups={"pricing": 1}),
        replace(extra=True),
    ],
    ids=[
        "missing-overridden",
        "missing-groups",
        "format-version-2",
        "negative-version",
        "bad-catalogue-version",
        "bad-created-at",
        "duplicate-overridden",
        "group-not-object",
        "extra-key",
    ],
)
def test_schema_rejects_broken_documents(break_document: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    validator = Draft202012Validator(schema(), format_checker=Draft202012Validator.FORMAT_CHECKER)
    with pytest.raises(ValidationError):
        validator.validate(break_document(overrides_document()))


def test_defaults_document_matches_build_document_with_no_overrides() -> None:
    from tunables.document import defaults_document

    assert defaults_document(catalogue, created_at=CREATED_AT) == build_document(
        catalogue, {}, version=0, created_at=CREATED_AT
    )


def test_defaults_document_stamps_now_by_default() -> None:
    from datetime import timedelta

    from django.utils import timezone

    from tunables.document import defaults_document

    before = timezone.now()
    created_at = datetime.fromisoformat(defaults_document(catalogue)["created_at"])
    assert created_at.tzinfo is not None
    assert before - timedelta(seconds=5) <= created_at <= timezone.now()


def test_environment_comes_from_the_argument() -> None:
    from tunables.document import defaults_document

    assert defaults_document(catalogue, created_at=CREATED_AT, environment="offline")["environment"] == "offline"
    assert defaults_document(catalogue, created_at=CREATED_AT)["environment"] == ""


@override_settings(TUNABLES={"CATALOGUE": "tests.catalogue.catalogue", "ENVIRONMENT": "staging"})
def test_the_setting_no_longer_reaches_the_document() -> None:
    from tunables.document import defaults_document

    assert defaults_document(catalogue, created_at=CREATED_AT)["environment"] == ""


def test_the_timestamp_fallback_is_utc() -> None:
    from datetime import UTC, datetime, timedelta

    from tunables.document import defaults_document

    stamped = datetime.fromisoformat(defaults_document(catalogue)["created_at"])
    assert stamped.utcoffset() == timedelta(0)
    assert abs(stamped - datetime.now(UTC)) < timedelta(seconds=5)


def test_the_document_and_the_schema_build_with_no_django_settings() -> None:
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).parent / "offline_document.py"
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    reported = json.loads(result.stdout)
    assert reported["environment"] == "offline"
    # No argument and no settings: the environment is empty rather than an ImproperlyConfigured.
    assert reported["default_environment"] == ""
    assert reported["version"] == 0
    assert reported["id"] == f"urn:tunables:snapshot:v1:{reported['catalogue_version']}"
