import re
from typing import Any

import pytest

from tests.catalogue import CATEGORIES, catalogue, currencies_within_limit, limits, pricing, thermostat, weights
from tunables import Catalogue, Category, Float, Group, Integer, Tunable
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
    assert Catalogue([pricing, thermostat, weights, limits], categories=CATEGORIES).version == catalogue.version


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
    assert (
        Catalogue([pricing], categories=CATEGORIES, label="a").version
        != Catalogue([pricing], categories=CATEGORIES).version
    )
    assert (
        Catalogue([pricing], categories=CATEGORIES, label="a").version
        != Catalogue([pricing], categories=CATEGORIES, label="b").version
    )


def test_version_depends_on_group_order() -> None:
    first = Catalogue([Group("a", [tunable()], order=1), Group("b", [tunable()], order=2)])
    second = Catalogue([Group("a", [tunable()], order=2), Group("b", [tunable()], order=1)])
    assert first.version != second.version


def test_version_ignores_declaration_order() -> None:
    assert (
        Catalogue([pricing, thermostat], categories=CATEGORIES).version
        == Catalogue([thermostat, pricing], categories=CATEGORIES).version
    )


def test_catalogue_validators_are_stored_and_do_not_affect_the_version() -> None:
    assert Catalogue([pricing], categories=CATEGORIES).validators == ()
    assert catalogue.validators == (currencies_within_limit,)
    assert Catalogue([pricing, thermostat, weights, limits], categories=CATEGORIES).version == catalogue.version


@pytest.mark.parametrize("field_name", ["metadata", "ui"])
@pytest.mark.parametrize("kind", ["tunable", "group"])
def test_metadata_and_ui_must_be_json_serialisable(kind: str, field_name: str) -> None:
    bad = {"owners": {"ops", "dev"}}
    with pytest.raises(CatalogueError) as info:
        if kind == "tunable":
            Tunable("x", Integer(), 1, **{field_name: bad})
        else:
            Group("g", [tunable()], **{field_name: bad})
    message = str(info.value)
    assert field_name in message
    assert ("tunable 'x'" if kind == "tunable" else "group 'g'") in message
    assert "not JSON serialisable" in message


def test_nan_in_metadata_is_refused() -> None:
    with pytest.raises(CatalogueError, match="metadata of tunable 'x'"):
        Tunable("x", Integer(), 1, metadata={"ratio": float("nan")})


def test_nested_json_values_are_accepted() -> None:
    value = {"owners": ["ops", "dev"], "limits": {"soft": 1, "hard": 2.5}, "enabled": True, "note": None}
    assert Tunable("x", Integer(), 1, metadata=value, ui=value).metadata == value
    assert Group("g", [tunable()], metadata=value, ui={**value, "sections": []}).ui["limits"] == value["limits"]


@pytest.mark.parametrize("name", ["Shop", "1x", "a-b", ""])
def test_category_rejects_bad_identifier(name: str) -> None:
    with pytest.raises(CatalogueError):
        Category(name)


def test_duplicate_category_and_undeclared_category_are_rejected() -> None:
    with pytest.raises(CatalogueError, match="duplicate category 'shop'"):
        Catalogue([Group("g", [tunable()])], categories=[Category("shop"), Category("shop")])
    with pytest.raises(CatalogueError, match="group 'g' names undeclared category 'shop'"):
        Catalogue([Group("g", [tunable()], category="shop")])


def test_general_category_is_implicit_and_can_be_declared() -> None:
    plain = Catalogue([Group("g", [tunable()])])
    assert list(plain.categories) == ["general"]
    assert (plain.categories["general"].title, plain.categories["general"].order) == ("General", 0)
    assert plain.groups["g"].category == "general"
    declared = Catalogue(
        [Group("g", [tunable()])], categories=[Category("general", title="Misc", order=9), Category("a")]
    )
    assert list(declared.categories) == ["a", "general"]
    assert declared.categories["general"].title == "Misc"


def test_groups_are_ordered_by_category_then_group() -> None:
    c = Catalogue(
        [
            Group("z", [tunable()], order=1, category="late"),
            Group("b", [tunable()], order=2, category="early"),
            Group("a", [tunable()], order=1, category="early"),
            Group("m", [tunable()], order=0),
            Group("n", [tunable()], order=0, category="tie"),
        ],
        categories=[Category("late", order=5), Category("early", order=1), Category("tie", order=1)],
    )
    assert list(c.categories) == ["general", "early", "tie", "late"]
    assert list(c.groups) == ["m", "a", "b", "n", "z"]
    assert [g.name for g in c.groups_in("early")] == ["a", "b"]
    assert c.groups_in("general") == (c.groups["m"],)
    with pytest.raises(CatalogueError, match="unknown category 'nope'"):
        c.groups_in("nope")


@pytest.mark.parametrize("tags", [["Money"], ["-x"], ["a b"], ["a", "a"], [""]])
def test_bad_tags_are_rejected(tags: list[str]) -> None:
    with pytest.raises(CatalogueError):
        Tunable("x", Integer(), 1, tags=tags)


def test_tags_are_stored_as_a_tuple() -> None:
    t = Tunable("x", Integer(), 1, tags=["money", "q3-2026", "a_b"])
    assert t.tags == ("money", "q3-2026", "a_b")
    assert Tunable("x", Integer(), 1).tags == ()


def test_version_ignores_categories_and_tags() -> None:
    base = Catalogue([Group("g", [Tunable("x", Integer(), 1)])])
    with_category = Catalogue([Group("g", [Tunable("x", Integer(), 1)], category="c")], categories=[Category("c")])
    with_tag = Catalogue([Group("g", [Tunable("x", Integer(), 1, tags=["t"])])])
    assert base.version == with_category.version == with_tag.version
    two = [Group("a", [tunable()], order=1), Group("b", [tunable()], order=2)]
    moved = [Group("a", [tunable()], order=1, category="late"), Group("b", [tunable()], order=2)]
    assert Catalogue(two).version == Catalogue(moved, categories=[Category("late", order=9)]).version
    assert list(Catalogue(moved, categories=[Category("late", order=9)]).groups) == ["b", "a"]


def test_shared_catalogue_categories_and_tags() -> None:
    assert list(catalogue.categories) == ["shop", "building", "general"]
    assert [g.name for g in catalogue.groups_in("general")] == ["weights", "limits"]
    assert catalogue.get("pricing.vat_rate").tags == ("money",)
    assert catalogue.get("limits.max_currencies").tags == ("money",)
    assert catalogue.get("thermostat.mode").tags == ("comfort",)
    assert catalogue.get("weights.alpha").tags == ()
