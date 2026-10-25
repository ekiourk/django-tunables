"""The demo catalogue: the README quickstart groups plus a group with a rule, kept out of EDITABLE_GROUPS."""

from tunables import Boolean, Catalogue, Enum, Float, Group, List, Tunable, sums_to

pricing = Group(
    "pricing",
    title="Pricing",
    order=1,
    tunables=[
        Tunable("vat_rate", Float(min=0.0, max=1.0), 0.24, title="VAT rate"),
        Tunable("free_shipping_over", Float(min=0.0), 50.0, title="Free shipping over", unit="EUR"),
        Tunable("currencies", List(Enum(["EUR", "USD", "GBP"]), min_items=1, unique=True), ["EUR"]),
        Tunable("allow_backorders", Boolean(), False),
    ],
)

thermostat = Group(
    "thermostat",
    title="Thermostat",
    order=2,
    tunables=[
        Tunable("target_c", Float(min=5.0, max=30.0), 21.0, title="Target temperature", unit="°C"),
        Tunable("mode", Enum(["auto", "heat", "cool", "off"]), "auto"),
    ],
)

weights = Group(
    "weights",
    title="Ranking weights",
    description="Only superusers may change these; see demo.access.",
    order=3,
    tunables=[
        Tunable("price", Float(min=0.0, max=1.0), 0.5),
        Tunable("rating", Float(min=0.0, max=1.0), 0.3),
        Tunable("distance", Float(min=0.0, max=1.0), 0.2),
    ],
    validators=[sums_to(1.0, "price", "rating", "distance")],
)

catalogue = Catalogue([pricing, thermostat, weights])
