# Declaring the catalogue

The catalogue is the host project's declaration of every tunable parameter: its name,
type, default, constraints, and how it is grouped. It lives in Python and is the source
of truth for structure and defaults. The database only stores overrides and history.

```python
from tunables import Boolean, Catalogue, Enum, Float, Group, List, Tunable

pricing = Group(
    "pricing",
    title="Pricing",
    order=1,
    tunables=[
        Tunable("vat_rate", Float(min=0.0, max=1.0), 0.24, title="VAT rate"),
        Tunable("free_shipping_over", Float(min=0.0), 50.0, unit="EUR"),
        Tunable("currencies", List(Enum(["EUR", "USD", "GBP"]), min_items=1, unique=True), ["EUR"]),
        Tunable("allow_backorders", Boolean(), False),
    ],
)

catalogue = Catalogue([pricing])
```

Point the setting at it and run `tunables_sync` after every deployment:

```python
TUNABLES = {"CATALOGUE": "myproject.tunables_catalogue.catalogue"}
```

`CATALOGUE` is a dotted path to a `Catalogue` instance or to a function without
arguments that returns one. The registry imports it once and caches it. Tests that
change the setting with `override_settings` get a fresh catalogue automatically;
code that builds catalogues at runtime can call `tunables.registry.reset()`.

## Tunable

```python
Tunable(name, type, default, title="", description="", unit="", ui={}, metadata={}, deprecated="", tags=())
```

| Argument | Meaning |
|---|---|
| `name` | Identifier matching `^[a-z][a-z0-9_]*$`. The full key is `<group>.<name>`. |
| `type` | A `TunableType` instance, see below. |
| `default` | Must satisfy the type. It is coerced and validated when the `Tunable` is constructed, so a default of `50` for a `Float` is stored as `50.0`, and an invalid default raises `CatalogueError` at import time. |
| `title`, `description` | Shown in the admin, the API and the JSON Schema. Lazy translation strings are accepted. |
| `unit` | Free text such as `"EUR"` or `"s"`. Shown next to the field and exposed as `x-unit`. |
| `ui` | Hints copied into the UI schema's `options` for this control, see [Sections and UI hints](#sections-and-ui-hints). Must be JSON serialisable; checked at construction. |
| `metadata` | Opaque to the package. Exposed read-only through the API. Must be JSON serialisable; checked at construction. |
| `deprecated` | A non-empty string marks the tunable deprecated with that reason. |
| `tags` | Seed tags, names matching `^[a-z0-9][a-z0-9_-]*$`, each once. See [Categories and tags](#categories-and-tags). |

A deprecated tunable still exists and can still be changed. Writes that touch it return
a warning, the JSON Schema marks it `deprecated`, the UI schema makes its control
read-only unless `ui` says otherwise, and the admin form shows the reason. Remove the
tunable from the catalogue once nothing reads it.

## Group

```python
Group(name, tunables, title="", description="", order=0, validators=(), ui={}, metadata={}, category="general")
```

`name` follows the identifier rule and is unique in the catalogue. Tunable names are
unique within the group. `category` names a declared `Category`, or the implicit
`general`. Groups are ordered by their category's order, then `(order, name)`,
everywhere they are listed.

`validators` is a sequence of callables that receive the effective values of the whole
group as a mapping from tunable name to Python value, including values that are not
being changed, and raise `ConstraintError("group", message)` when the combination is
invalid:

```python
from tunables.errors import ConstraintError


def weights_sum_to_one(values):
    """The three weights must sum to 1."""
    if abs(values["alpha"] + values["beta"] + values["gamma"] - 1.0) > 1e-9:
        raise ConstraintError("group", "weights must sum to 1")
```

The validator's description is shown to people in the admin group index and to
clients in the JSON Schema's `x-validators`. It is taken from a `description`
attribute on the callable if present, else from the first paragraph of its docstring,
else from its name. Cross-field rules cannot be expressed in JSON Schema, which is why
the API has dry-run endpoints.

## Catalogue

```python
Catalogue(groups, *, categories=(), label="", validators=())
```

| Member | Meaning |
|---|---|
| `categories` | Mapping of name to `Category`, in `(order, name)` order; `general` is always present. |
| `groups` | Mapping of name to `Group`, ordered by category, then `(order, name)`. |
| `groups_in(category)` | The groups of one category in that order, or `CatalogueError`. |
| `get(key)` | The `Tunable` for `"group.name"`, or `UnknownKey`. |
| `group_of(key)` | The `Group` a key belongs to, or `UnknownKey`. |
| `keys()` | Every full key in group order, then tunable order. |
| `defaults()` | `{group: {name: default}}` with Python values. |
| `version` | `"sha256:<hex>"`, see [Catalogue version](#catalogue-version). |

Duplicate group names raise `CatalogueError`.

### Categories and tags

Categories are a navigation level above groups, for the admin index and the API's
category listing. They carry no values and no validators.

```python
from tunables import Category

shop = Category("shop", title="Shop", order=1)
catalogue = Catalogue([pricing, thermostat], categories=[shop])
```

A group names its category with `category="shop"`. Groups that name none belong to
`general`, which exists even when undeclared, with title "General" and order 0.
Declaring `Category("general", ...)` replaces that default, for example to give it a
different title or to order it last. A group naming an undeclared category, or two
categories with one name, is a `CatalogueError`.

Tags are free-form labels on tunables for search across groups. `Tunable(tags=[...])`
declares seed tags, which `tunables_sync` creates and re-applies on every run; operators
can attach further tags by hand. Tag names match `^[a-z0-9][a-z0-9_-]*$`.

Categories and tags change how definitions are listed and found. They do not change
values, snapshots, or the catalogue version, so adding them to an existing deployment
writes no new version.

### Catalogue validators

Group validators see one group. A rule that spans groups goes on the catalogue:

```python
def currencies_within_limit(values):
    """The number of accepted currencies must not exceed limits.max_currencies."""
    if len(values["pricing"]["currencies"]) > values["limits"]["max_currencies"]:
        raise ConstraintError("catalogue", "accepted currencies exceed limits.max_currencies")


catalogue = Catalogue([pricing, limits], validators=[currencies_within_limit])
```

A catalogue validator receives the effective values of every group, as a mapping of
group name to a mapping of tunable name to Python value, and raises `ConstraintError`
when the combination is invalid. It runs after the group validators on every write and
dry run, whichever group the write touches, so a change in one group can be refused
because of a value in another. The message should therefore name the values it compared.
Errors are reported with scope `catalogue` in the API problem body, as non-field errors
in the admin, and as `catalogue: <code>: <message>` lines by `tunables_import`. The
description, found the same way as for group validators, appears in the document schema's
top-level `x-validators`, in the `status/` endpoint and on the admin group index.
Validators do not affect the catalogue version.

## Built-in types

| Type | Constraints | Python value | JSON value | Error codes |
|---|---|---|---|---|
| `Integer(min=None, max=None)` | inclusive bounds | `int` | number | `min`, `max` |
| `Float(min=None, max=None)` | inclusive bounds | `float` | number | `min`, `max` |
| `Boolean()` | | `bool` | boolean | |
| `String(min_length=None, max_length=None, pattern=None)` | length, regex | `str` | string | `min_length`, `max_length`, `pattern` |
| `Enum(choices)` | membership | `str` | string | `enum` |
| `List(item, min_items=None, max_items=None, unique=False)` | size, uniqueness, per item | `list` | array | `min_items`, `max_items`, `unique`, plus the item's codes |
| `Mapping(key, value, min_entries=None, max_entries=None)` | size, per key, per value | `dict` | object | `min_entries`, `max_entries`, plus the key and value codes |

Every type also has the code `type` for input that is not of the right JSON kind.

Coercion and validation rules:

- Coercion from JSON is strict. `"30"` is not an integer, `"true"` is not a boolean,
  and `true` is not an integer or a number even though Python's `bool` is a subclass of
  `int`. The one relaxation is that `Float` accepts a JSON integer such as `50`.
- `Float` rejects NaN and infinity because they cannot be written to the snapshot document.
- `String.pattern` is matched with `re.search`, unanchored, which is what JSON Schema's
  `pattern` means. Write `^...$` for a full match.
- `List(unique=True)` compares items by their JSON form, so lists of lists work.
- Errors inside a list carry the item's code and a message prefixed with the index, such
  as `[2]: must be >= 0`. Errors inside a mapping are prefixed with the key, such as
  `["EUR"]: must be >= 0`.
- `Mapping` keys are JSON object keys and therefore strings. The key type must produce
  strings: `String`, possibly with a pattern, or `Enum`. A lookup from a pair of integers
  is declared as `Mapping(String(pattern=r"^\d+,\d+$"), Float())` with keys like `"3,5"`.
  A declaration whose default has a key the key type refuses fails at import. The class
  shadows `collections.abc.Mapping` in modules that import both, so alias one of them.
- The admin uses `IntegerField`, `FloatField`, `BooleanField`, `CharField`,
  `ChoiceField`, and `JSONField` for both `List` and `Mapping`, each carrying a validator
  that enforces the same constraints as the API.

## Custom types

Subclass `TunableType` and implement its six methods. No registration step exists:
the definition mirror stores `describe()`, which is how the API shows the type to clients.

```python
from typing import Any, ClassVar

from django import forms

from tunables import TunableType
from tunables.errors import ConstraintError, TypeCoercionError


class HexColour(TunableType):
    name: ClassVar[str] = "hex_colour"

    def coerce(self, raw: Any) -> str:
        if not isinstance(raw, str):
            raise TypeCoercionError("expected a string")
        return raw.lower()

    def validate(self, value: str) -> None:
        if len(value) != 7 or not value.startswith("#"):
            raise ConstraintError("format", "must look like #rrggbb")

    def to_json(self, value: str) -> str:
        return value

    def json_schema(self) -> dict[str, Any]:
        return {"type": "string", "pattern": "^#[0-9a-f]{6}$"}

    def form_field(self, **kwargs: Any) -> forms.Field:
        return forms.CharField(**kwargs)

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "params": {}}
```

| Method | Contract |
|---|---|
| `coerce(raw)` | JSON input to the Python value. Raise `TypeCoercionError` for the wrong kind of input. Must accept its own `to_json()` output. |
| `validate(value)` | Check constraints on a coerced value. Raise `ConstraintError(code, message)` with a stable code. |
| `to_json(value)` | The JSON-serialisable form written to snapshots. |
| `json_schema()` | A draft 2020-12 fragment describing one value. |
| `form_field(**kwargs)` | A Django form field. Pass `kwargs` through; they carry `label`, `help_text`, `initial` and `required`. |
| `describe()` | `{"name": ..., "params": {...}}` with every parameter present even when unset. It feeds the catalogue version hash, so keep it stable. |

The built-in types are frozen dataclasses, which gives them equality and a readable
`repr`. A custom type can do the same.

## Sections and UI hints

`Tunable.ui` is copied into the `options` of that tunable's control in the UI schema.
The package interprets only `readonly`, which it sets to `true` for deprecated
tunables unless the declaration says otherwise. Everything else passes through for
the client's renderer.

`Group.ui["sections"]` groups controls in the UI schema and is validated when the
`Group` is constructed. Every name must be a tunable of the group and may appear once.
Tunables not named in any section follow the sections as plain controls.

```python
Group(
    "thermostat",
    ui={
        "sections": [
            {"title": "Control", "tunables": ["target_c", "mode"]},
            {"title": "Sampling", "tunables": ["sample_interval"]},
        ]
    },
    tunables=[...],
)
```

## Catalogue version

`Catalogue.version` is a SHA-256 over the parts of the declaration that change the
meaning of stored data: for each group in order, for each tunable in order, its name,
`type.describe()`, the JSON form of its default, and whether it is deprecated. The
label is included when set.

Titles, descriptions, units, `ui`, `metadata`, categories and tags are not hashed, so
rewording a label or reorganising groups does not create a new version. Adding, removing, renaming or retyping a tunable,
changing a default, deprecating a tunable, or changing a group's `order` does.

The version is stored with every change set and snapshot and compared on every request.
When the code and the database disagree, the API answers `503` and the admin refuses to
render the edit form until `tunables_sync` has run. See the management commands in the
README for what `tunables_sync` does when the catalogue has changed.
