import re
from typing import Any

import pytest

from tests.catalogue import catalogue, currencies_within_limit, limits, pricing, thermostat, weights
from tunables import Catalogue, Float, Group, Integer, Tunable
from tunables.errors import CatalogueError, UnknownKey


def tunable(name: str = "x", **kwargs: Any) -> Tunable:
    return Tunable(name, Integer(), 1, **kwargs)


@pytest.mark.parametrize("name", ["Vat", "1x", "a-b", "", "a.b", "a b"])
def test_tunable_rejects_bad_identifier(name: str) -> None:
    with pytest.raises(CatalogueError):
        Tunable(name, Integer(), 1)


@pytest.mark.parametrize("name", ["Vat", "1x", "a-b", ""])
def test_group_rejects_bad_identifier(name: str) -> None:
    with pytest.raises(CatalogueError):
        Group(name, [])


def test_tunable_rejects_default_failing_constraint() -> None:
    with pytest.raises(CatalogueError, match="rate"):
        Tunable("rate", Float(min=0.0), -1.0)


def test_tunable_rejects_default_of_wrong_type() -> None:
    with pytest.raises(CatalogueError, match="count"):
        Tunable("count", Integer(), "3")


def test_tunable_stores_coerced_default() -> None:
    t = Tunable("rate", Float(), 50)
    assert t.default == 50.0
    assert type(t.default) is float


def test_group_rejects_duplicate_tunable_names() -> None:
    with pytest.raises(CatalogueError, match="dup"):
        Group("g", [tunable("dup"), tunable("dup")])


def test_group_stores_tunables_as_tuple() -> None:
    assert isinstance(Group("g", [tunable()]).tunables, tuple)


def test_catalogue_rejects_duplicate_group_names() -> None:
    with pytest.raises(CatalogueError, match="g"):
        Catalogue([Group("g", [tunable()]), Group("g", [tunable()])])


def test_groups_ordered_by_order_then_name() -> None:
    c = Catalogue(
        [
            Group("zeta", [tunable()], order=1),
            Group("beta", [tunable()], order=2),
            Group("alpha", [tunable()], order=1),
        ]
    )
    assert list(c.groups) == ["alpha", "zeta", "beta"]


def test_shared_catalogue_group_order() -> None:
    assert list(catalogue.groups) == ["pricing", "thermostat", "weights", "limits"]
    assert catalogue.groups["pricing"] is pricing


def test_get_returns_tunable() -> None:
    assert catalogue.get("pricing.vat_rate") is pricing.tunables[0]


def test_group_of_returns_group() -> None:
    assert catalogue.group_of("thermostat.mode") is thermostat


@pytest.mark.parametrize("key", ["vat_rate", "shop.vat_rate", "pricing.discount", "pricing.vat_rate.extra", ""])
def test_unknown_key(key: str) -> None:
    with pytest.raises(UnknownKey) as info:
        catalogue.get(key)
    assert info.value.key == key
    with pytest.raises(UnknownKey):
        catalogue.group_of(key)


def test_keys_follow_group_then_tunable_order() -> None:
    assert list(catalogue.keys()) == [
        "pricing.vat_rate",
        "pricing.free_shipping_over",
        "pricing.currencies",
        "pricing.shipping_rates",
        "pricing.allow_backorders",
        "thermostat.target_c",
        "thermostat.mode",
        "thermostat.sample_interval",
        "thermostat.display_colour",
        "thermostat.legacy_offset",
        "weights.alpha",
        "weights.beta",
        "weights.gamma",
        "limits.max_currencies",
    ]


def test_defaults() -> None:
    assert catalogue.defaults() == {
        "pricing": {
            "vat_rate": 0.24,
            "free_shipping_over": 50.0,
            "currencies": ["EUR"],
            "shipping_rates": {"EUR": 4.9},
            "allow_backorders": False,
        },
        "thermostat": {
            "target_c": 21.0,
            "mode": "auto",
            "sample_interval": 60.0,
            "display_colour": "#ffffff",
            "legacy_offset": 0.0,
        },
        "weights": {"alpha": 0.5, "beta": 0.3, "gamma": 0.2},
        "limits": {"max_currencies": 3},
    }


def test_defaults_returns_a_fresh_dict() -> None:
    catalogue.defaults()["weights"]["alpha"] = 1.0
    assert catalogue.defaults()["weights"]["alpha"] == 0.5


def test_version_format_and_stability() -> None:
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", catalogue.version)
    assert Catalogue([pricing, thermostat, weights, limits]).version == catalogue.version


def make(**changes: Any) -> Catalogue:
    fields: dict[str, Any] = {"name": "x", "type": Integer(min=0), "default": 1, "deprecated": ""}
    fields.update(changes)
    return Catalogue([Group("g", [Tunable(**fields)])])


BASE = make()


@pytest.mark.parametrize(
    "changes",
    [
        {"type": Integer(min=1)},
        {"type": Float()},
        {"default": 2},
        {"deprecated": "gone"},
        {"name": "y"},
    ],
)
def test_version_changes_on_structural_change(changes: dict[str, Any]) -> None:
    assert make(**changes).version != BASE.version


@pytest.mark.parametrize(
    "changes",
    [
        {"title": "Title"},
        {"description": "Words"},
        {"unit": "kg"},
        {"ui": {"widget": "slider"}},
        {"metadata": {"owner": "ops"}},
    ],
)
def test_version_ignores_wording_and_hints(changes: dict[str, Any]) -> None:
    assert make(**changes).version == BASE.version


def test_version_includes_label() -> None:
    assert Catalogue([pricing], label="a").version != Catalogue([pricing]).version
    assert Catalogue([pricing], label="a").version != Catalogue([pricing], label="b").version


def test_version_depends_on_group_order() -> None:
    first = Catalogue([Group("a", [tunable()], order=1), Group("b", [tunable()], order=2)])
    second = Catalogue([Group("a", [tunable()], order=2), Group("b", [tunable()], order=1)])
    assert first.version != second.version


def test_version_ignores_declaration_order() -> None:
    assert Catalogue([pricing, thermostat]).version == Catalogue([thermostat, pricing]).version


def test_catalogue_validators_are_stored_and_do_not_affect_the_version() -> None:
    assert Catalogue([pricing]).validators == ()
    assert catalogue.validators == (currencies_within_limit,)
    assert Catalogue([pricing, thermostat, weights, limits]).version == catalogue.version
