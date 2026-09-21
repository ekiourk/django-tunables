from typing import Any

import pytest
from django.test import override_settings

from tunables import Actor, Catalogue, Change, Float, Group, Integer, String, Tunable, services
from tunables.catalogue import validator_description
from tunables.errors import CatalogueError, ConstraintError, ValidationFailed
from tunables.sync import sync
from tunables.validators import _NamedValidator, ascending, descending, describes, sums_to


def weights() -> list[Tunable]:
    return [
        Tunable("alpha", Float(min=0.0, max=1.0), 0.5),
        Tunable("beta", Float(min=0.0, max=1.0), 0.3),
        Tunable("gamma", Float(min=0.0, max=1.0), 0.2),
    ]


def thresholds() -> list[Tunable]:
    return [
        Tunable("critical_min", Float(min=0.0), 0.8),
        Tunable("high_min", Float(min=0.0), 0.6),
        Tunable("medium_min", Float(min=0.0), 0.3),
        Tunable("low_min", Float(min=0.0), 0.0),
    ]


def raised(validator: Any, values: dict[str, Any]) -> ConstraintError:
    with pytest.raises(ConstraintError) as info:
        validator(values)
    return info.value


def test_describes_sets_the_description_and_returns_the_function() -> None:
    def rule(values: dict[str, Any]) -> None:
        """Docstring that should lose to the decorator."""

    assert describes("The three weights must sum to 1.")(rule) is rule
    assert rule.description == "The three weights must sum to 1."  # type: ignore[attr-defined]
    assert validator_description(rule) == "The three weights must sum to 1."


def test_the_docstring_fallback_still_works() -> None:
    def rule(values: dict[str, Any]) -> None:
        """Weights must sum to 1."""

    assert validator_description(rule) == "Weights must sum to 1."


def test_sums_to_accepts_a_valid_total() -> None:
    rule = sums_to(1.0, "alpha", "beta", "gamma")
    assert rule({"alpha": 0.5, "beta": 0.3, "gamma": 0.2}) is None
    assert rule({"alpha": 0.5, "beta": 0.3, "gamma": 0.2 + 1e-10}) is None


def test_sums_to_reports_the_fields_and_the_total() -> None:
    rule = sums_to(1.0, "alpha", "beta", "gamma")
    error = raised(rule, {"alpha": 0.6, "beta": 0.3, "gamma": 0.2})
    assert error.code == "sum"
    assert error.message == "alpha + beta + gamma must sum to 1, got 1.1"


def test_sums_to_respects_its_tolerance() -> None:
    rule = sums_to(1.0, "alpha", "beta", tolerance=0.01)
    assert rule({"alpha": 0.5, "beta": 0.505}) is None
    assert raised(rule, {"alpha": 0.5, "beta": 0.52}).code == "sum"


def test_sums_to_describes_itself() -> None:
    assert validator_description(sums_to(1.0, "alpha", "beta", "gamma")) == "alpha + beta + gamma must sum to 1."
    assert validator_description(sums_to(2.5, "alpha", "beta")) == "alpha + beta must sum to 2.5."


def test_descending_accepts_valid_values() -> None:
    rule = descending("critical_min", "high_min", "medium_min", "low_min", floor=0.0)
    assert rule({"critical_min": 0.8, "high_min": 0.6, "medium_min": 0.3, "low_min": 0.0}) is None


def test_descending_names_the_offending_pair() -> None:
    rule = descending("critical_min", "high_min", "medium_min", "low_min")
    error = raised(rule, {"critical_min": 0.8, "high_min": 0.2, "medium_min": 0.3, "low_min": 0.0})
    assert error.code == "order"
    assert error.message == "high_min must be greater than medium_min, got 0.2 and 0.3"


def test_ascending_names_the_offending_pair() -> None:
    rule = ascending("low_min", "medium_min", "high_min")
    error = raised(rule, {"low_min": 0.0, "medium_min": 0.7, "high_min": 0.3})
    assert error.code == "order"
    assert error.message == "medium_min must be less than high_min, got 0.7 and 0.3"


def test_equal_neighbours_need_strict_false() -> None:
    values = {"critical_min": 0.8, "high_min": 0.8, "medium_min": 0.3}
    assert raised(descending("critical_min", "high_min", "medium_min"), values).code == "order"
    assert descending("critical_min", "high_min", "medium_min", strict=False)(values) is None
    rising = {"low_min": 0.0, "medium_min": 0.3, "high_min": 0.3}
    assert raised(ascending("low_min", "medium_min", "high_min"), rising).code == "order"
    assert ascending("low_min", "medium_min", "high_min", strict=False)(rising) is None


def test_floor_and_ceiling_pin_the_ends() -> None:
    rule = descending("critical_min", "high_min", "low_min", floor=0.0, ceiling=1.0)
    assert rule({"critical_min": 1.0, "high_min": 0.5, "low_min": 0.0}) is None
    low = raised(rule, {"critical_min": 1.0, "high_min": 0.5, "low_min": 0.1})
    assert (low.code, low.message) == ("floor", "low_min must be 0, got 0.1")
    high = raised(rule, {"critical_min": 0.9, "high_min": 0.5, "low_min": 0.0})
    assert (high.code, high.message) == ("ceiling", "critical_min must be 1, got 0.9")


def test_ascending_pins_the_other_ends() -> None:
    rule = ascending("low_min", "high_min", floor=0.0, ceiling=1.0)
    assert rule({"low_min": 0.0, "high_min": 1.0}) is None
    assert raised(rule, {"low_min": 0.2, "high_min": 1.0}).code == "floor"
    assert raised(rule, {"low_min": 0.0, "high_min": 0.7}).code == "ceiling"


def test_two_names_are_enough() -> None:
    assert descending("critical_min", "low_min")({"critical_min": 0.5, "low_min": 0.1}) is None
    assert raised(ascending("low_min", "critical_min"), {"low_min": 0.5, "critical_min": 0.1}).code == "order"


def test_ordering_validators_describe_themselves() -> None:
    assert validator_description(descending("critical_min", "high_min", "low_min", floor=0.0)) == (
        "critical_min, high_min and low_min descend strictly, and low_min is 0."
    )
    assert validator_description(ascending("low_min", "high_min", strict=False)) == (
        "low_min and high_min ascend, equal values allowed."
    )
    assert validator_description(descending("a_min", "b_min", ceiling=1.0)) == (
        "a_min and b_min descend strictly, and a_min is 1."
    )


def test_an_unknown_name_is_rejected_at_construction() -> None:
    with pytest.raises(CatalogueError, match="nope"):
        Group("weights", weights(), validators=[sums_to(1.0, "alpha", "nope")])
    with pytest.raises(CatalogueError, match="nope"):
        Group("levels", thresholds(), validators=[descending("critical_min", "nope")])


def test_a_non_numeric_name_is_rejected_at_construction() -> None:
    tunables = [Tunable("alpha", Float(), 0.5), Tunable("label", String(), "x")]
    with pytest.raises(CatalogueError, match="label"):
        Group("weights", tunables, validators=[sums_to(1.0, "alpha", "label")])


def test_integer_tunables_are_accepted() -> None:
    tunables = [Tunable("high", Integer(), 10), Tunable("low", Integer(), 1)]
    group = Group("limits", tunables, validators=[descending("high", "low")])
    assert group.validators[0]({"high": 10, "low": 1}) is None


balanced = Catalogue([Group("weights", weights(), validators=[sums_to(1.0, "alpha", "beta", "gamma")])])


@override_settings(TUNABLES={"CATALOGUE": f"{__name__}.balanced"})
def test_a_group_built_this_way_reports_a_group_error(db: None) -> None:
    sync()
    with pytest.raises(ValidationFailed) as info:
        services.apply_changeset([Change("weights.alpha", 0.9)], actor=Actor("alice", "verified"), source="api")
    error = info.value.errors[0]
    assert (error.group, error.code) == ("weights", "sum")
    assert error.message == "alpha + beta + gamma must sum to 1, got 1.4"


def test_a_rule_needs_at_least_two_names() -> None:
    for call in (
        lambda: sums_to(1.0),  # type: ignore[call-arg]
        lambda: sums_to(1.0, "alpha"),  # type: ignore[call-arg]
        lambda: descending(),  # type: ignore[call-arg]
        lambda: descending("critical_min"),  # type: ignore[call-arg]
        lambda: ascending("low_min"),  # type: ignore[call-arg]
    ):
        with pytest.raises(TypeError):
            call()


def test_a_group_rule_cannot_be_a_catalogue_validator() -> None:
    group = Group("weights", weights(), validators=[sums_to(1.0, "alpha", "beta", "gamma")])
    with pytest.raises(CatalogueError, match="group validator") as info:
        Catalogue([group], validators=[sums_to(1.0, "alpha", "beta")])
    assert "alpha + beta must sum to 1." in str(info.value)


class Ruled(_NamedValidator):
    """Alpha must stay under beta."""

    names = ("alpha",)

    def __call__(self, values: Any) -> None:
        pass


def test_a_rule_without_a_description_still_reports_itself() -> None:
    rule = Ruled()
    assert str(rule) == ""
    assert repr(rule) == "Ruled('')"
    with pytest.raises(CatalogueError, match="group validator") as info:
        Catalogue([Group("weights", weights())], validators=[rule])
    assert "Alpha must stay under beta." in str(info.value)


def test_a_rule_reprs_as_its_description() -> None:
    assert repr(sums_to(1.0, "alpha", "beta")) == "_SumsTo('alpha + beta must sum to 1.')"


def test_a_value_too_large_for_float_is_a_violation_not_a_crash() -> None:
    huge = 10**400
    error = raised(sums_to(1.0, "alpha", "beta"), {"alpha": huge, "beta": 0})
    assert error.code == "sum"
    assert error.message.endswith(f"must sum to 1, got {huge}")
    order = raised(descending("high", "low"), {"high": 0, "low": huge})
    assert order.code == "order"
    assert order.message == f"high must be greater than low, got 0 and {huge}"
    pin = raised(descending("high", "low", floor=0.0), {"high": huge + 1, "low": huge})
    assert pin.code == "floor"


def test_repeated_names_are_rejected_at_construction() -> None:
    with pytest.raises(CatalogueError, match="alpha"):
        Group("weights", weights(), validators=[sums_to(1.0, "alpha", "alpha", "beta")])
    with pytest.raises(CatalogueError, match="beta"):
        Group("weights", weights(), validators=[descending("beta", "beta")])
