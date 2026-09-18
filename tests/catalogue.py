from collections.abc import Mapping as ValueMapping
from typing import Any

from django import forms

from tunables import Boolean, Catalogue, Category, Enum, Float, Group, Integer, List, Mapping, Tunable, TunableType
from tunables.errors import ConstraintError, TypeCoercionError


class HexColour(TunableType):
    name = "hex_colour"

    def coerce(self, raw: Any) -> str:
        if not isinstance(raw, str):
            raise TypeCoercionError("expected a string")
        return raw.lower()

    def validate(self, value: Any) -> None:
        if len(value) != 7 or not value.startswith("#"):
            raise ConstraintError("format", "must look like #rrggbb")

    def to_json(self, value: Any) -> str:
        return str(value)

    def json_schema(self) -> dict[str, Any]:
        return {"type": "string", "pattern": "^#[0-9a-f]{6}$"}

    def form_field(self, **kwargs: Any) -> forms.Field:
        return forms.CharField(**kwargs)

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "params": {}}


def weights_sum_to_one(values: ValueMapping[str, Any]) -> None:
    """The three weights must sum to 1."""
    if abs(values["alpha"] + values["beta"] + values["gamma"] - 1.0) > 1e-9:
        raise ConstraintError("group", "weights must sum to 1")


shop = Category("shop", title="Shop", order=1)
building = Category("building", title="Building", order=2)
general = Category("general", title="General", description="Everything else.", order=3)
CATEGORIES = [shop, building, general]

pricing = Group(
    "pricing",
    title="Pricing",
    description="Prices and shipping rules for the web shop.",
    order=1,
    category="shop",
    tunables=[
        Tunable("vat_rate", Float(min=0.0, max=1.0), 0.24, title="VAT rate", tags=["money"]),
        Tunable("free_shipping_over", Float(min=0.0), 50.0, title="Free shipping over", unit="EUR"),
        Tunable(
            "currencies",
            List(Enum(["EUR", "USD", "GBP"]), min_items=1, unique=True),
            ["EUR"],
            title="Accepted currencies",
        ),
        Tunable(
            "shipping_rates",
            Mapping(Enum(["EUR", "USD", "GBP"]), Float(min=0.0)),
            {"EUR": 4.9},
            title="Shipping rates",
            unit="per currency",
            tags=["money"],
        ),
        Tunable("allow_backorders", Boolean(), False, title="Allow backorders"),
    ],
)

thermostat = Group(
    "thermostat",
    title="Thermostat",
    order=2,
    category="building",
    ui={
        "sections": [
            {"title": "Control", "tunables": ["target_c", "mode"]},
            {"title": "Sampling", "tunables": ["sample_interval"]},
        ]
    },
    tunables=[
        Tunable("target_c", Float(min=5.0, max=30.0), 21.0, title="Target temperature", unit="°C", tags=["comfort"]),
        Tunable("mode", Enum(["auto", "heat", "cool", "off"]), "auto", title="Mode", tags=["comfort"]),
        Tunable("sample_interval", Float(min=1.0), 60.0, title="Sample interval", unit="s"),
        Tunable("display_colour", HexColour(), "#ffffff", title="Display colour"),
        Tunable("legacy_offset", Float(), 0.0, title="Legacy offset", deprecated="Use target_c instead."),
    ],
)

weights = Group(
    "weights",
    title="Weights",
    order=3,
    tunables=[
        Tunable("alpha", Float(min=0.0, max=1.0), 0.5),
        Tunable("beta", Float(min=0.0, max=1.0), 0.3),
        Tunable("gamma", Float(min=0.0, max=1.0), 0.2),
    ],
    validators=[weights_sum_to_one],
)

limits = Group(
    "limits",
    title="Limits",
    order=4,
    tunables=[Tunable("max_currencies", Integer(min=1), 3, title="Maximum accepted currencies", tags=["money"])],
)


def currencies_within_limit(values: ValueMapping[str, ValueMapping[str, Any]]) -> None:
    """The number of accepted currencies must not exceed limits.max_currencies."""
    if len(values["pricing"]["currencies"]) > values["limits"]["max_currencies"]:
        raise ConstraintError("catalogue", "accepted currencies exceed limits.max_currencies")


catalogue = Catalogue(
    [pricing, thermostat, weights, limits],
    categories=CATEGORIES,
    validators=[currencies_within_limit],
)
