"""Django forms over a set of tunables, shared by the admin's group editor and the embedded panel."""

import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, get_args

from django import forms
from django.http import QueryDict
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

from tunables.catalogue import Catalogue, Tunable
from tunables.changes import Actor, Change
from tunables.errors import FieldError, NothingToChange, UnknownVersion, ValidationFailed, VersionConflict
from tunables.registry import get_catalogue
from tunables.services import ChangeResult, apply_changeset, diff_versions, latest_snapshot

ReasonMode = Literal["required", "optional", "hidden"]
REASON_MODES: tuple[str, ...] = get_args(ReasonMode)

SHOWN_WIDTH = 40


@dataclass(frozen=True)
class Row:
    """One tunable of a bound or unbound form, ready for a template."""

    tunable: Tunable
    field: forms.BoundField
    reset: forms.BoundField | None
    shown: str


class TunablesForm(forms.Form):
    """Base for the forms built by form_class. Fields are named by full key; a reset box is ``reset_<key>``."""

    reason = forms.CharField(label=_("Reason"), max_length=1000, widget=forms.Textarea(attrs={"rows": 2}))
    expected_version = forms.IntegerField(widget=forms.HiddenInput)

    keys: Sequence[str]
    tunables: Mapping[str, Tunable]
    values: Mapping[str, Any]
    reason_mode: ReasonMode
    conflict: VersionConflict | None = None

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        for key in self.keys:
            if key in self.errors or cleaned.get(f"reset_{key}"):
                continue
            if cleaned.get(key) is None:
                self.add_error(key, _("Enter a value or tick reset to default."))
        return cleaned

    def changes(self) -> list[Change]:
        """One change per key: a reset when its box is ticked, else the cleaned value."""
        changes = []
        for key in self.keys:
            if self.cleaned_data.get(f"reset_{key}"):
                changes.append(Change(key, reset=True))
            else:
                changes.append(Change(key, self.tunables[key].type.to_json(self.cleaned_data[key])))
        return changes

    def save(
        self, *, actor: Actor, source: str, reason: str = "", metadata: Mapping[str, Any] | None = None
    ) -> ChangeResult | None:
        """Apply the changes of a valid form as one change set.

        The reason is the one entered in the form; ``reason`` is the fallback when the field is empty or
        hidden. Returns the result, or None when nothing was written: validation errors and "nothing changed"
        land on the form, and a version conflict is kept in ``conflict`` for ``rebound()`` to resolve.
        """
        try:
            return apply_changeset(
                self.changes(),
                actor=actor,
                source=source,
                reason=self.cleaned_data.get("reason") or reason,
                expected_version=self.cleaned_data["expected_version"],
                metadata=metadata,
            )
        except ValidationFailed as error:
            for item in error.errors:
                field = item.key if isinstance(item, FieldError) and item.key in self.fields else None
                self.add_error(field, item.message)
        except NothingToChange:
            self.add_error(None, _("Nothing changed."))
        except VersionConflict as conflict:
            self.conflict = conflict
        return None

    def rebound(self, document: Mapping[str, Any] | None = None) -> "TunablesForm":
        """After a conflict: the same input bound against the conflicting version, validated, with the conflict kept."""
        assert self.conflict is not None
        data: Any = self.data.copy() if isinstance(self.data, QueryDict) else dict(self.data)
        data["expected_version"] = str(self.conflict.actual)
        document = document or latest_snapshot(self.conflict.actual).document
        form = build_form(self.keys, document, reason=self.reason_mode)(data)
        form.is_valid()
        form.conflict = self.conflict
        return form

    def conflict_text(self) -> str:
        """The warning for the conflict kept on this form, naming the keys of the form that moved."""
        assert self.conflict is not None
        try:
            entries = diff_versions(self.conflict.expected, self.conflict.actual)
            moved = [entry.key for entry in entries if entry.key in self.keys]
        except UnknownVersion:
            moved = []
        context = {"expected": self.conflict.expected, "actual": self.conflict.actual, "names": ", ".join(moved)}
        if moved:
            text = _(
                "The values changed while you were editing: version %(expected)s is now version "
                "%(actual)s, and these changed: %(names)s. Your input is kept; review and submit again."
            )
        else:
            text = _(
                "The values changed while you were editing: version %(expected)s is now version "
                "%(actual)s. Your input is kept; review and submit again."
            )
        return text % context

    def rows(self) -> list[Row]:
        """The tunables in form order with their bound fields and current values as text."""
        return [
            Row(
                tunable=self.tunables[key],
                field=self[key],
                reset=self[f"reset_{key}"] if f"reset_{key}" in self.fields else None,
                shown=value_text(self.tunables[key], self.values[key]),
            )
            for key in self.keys
        ]


def value_text(tunable: Tunable, value: Any) -> str:
    """A value as JSON with its unit, the way the API shows it."""
    rendered = json.dumps(tunable.type.to_json(value), ensure_ascii=False)
    return f"{rendered} {tunable.unit}" if tunable.unit else rendered


def form_class(
    tunables: Mapping[str, Tunable],
    values: Mapping[str, Any],
    overridden: Collection[str],
    version: int,
    *,
    reason: ReasonMode = "required",
) -> type[TunablesForm]:
    """A TunablesForm subclass with one field per key, initial values, and reset boxes for overrides.

    ``tunables`` maps full keys to their definitions in display order, ``values`` holds the effective
    value of each, and ``overridden`` names the keys that currently carry an override.
    """
    if reason not in REASON_MODES:
        raise ValueError(f"reason must be one of {REASON_MODES}, not {reason!r}")
    attrs: dict[str, Any] = {
        "keys": tuple(tunables),
        "tunables": dict(tunables),
        "values": dict(values),
        "reason_mode": reason,
    }
    for key, tunable in tunables.items():
        parts = [str(tunable.description), f"({tunable.unit})" if tunable.unit else ""]
        attrs[key] = tunable.type.form_field(
            label=str(tunable.title) or tunable.name,
            help_text=" ".join(part for part in parts if part),
            initial=values[key],
            required=False,
        )
        if key in overridden:
            full = value_text(tunable, tunable.default)
            shown = full if len(full) <= SHOWN_WIDTH else full[: SHOWN_WIDTH - 1] + "…"
            attrs[f"reset_{key}"] = forms.BooleanField(
                label=format_lazy(_("Reset to default ({value})"), value=shown),
                help_text="" if shown == full else full,
                required=False,
            )
    attrs["expected_version"] = forms.IntegerField(widget=forms.HiddenInput, initial=version)
    if reason != "required":
        attrs["reason"] = forms.CharField(
            label=_("Reason"),
            max_length=1000,
            required=False,
            widget=forms.HiddenInput() if reason == "hidden" else forms.Textarea(attrs={"rows": 2}),
        )
    return type("TunablesForm", (TunablesForm,), attrs)


def build_form(
    keys: Sequence[str],
    document: Mapping[str, Any],
    *,
    reason: ReasonMode = "required",
    catalogue: Catalogue | None = None,
) -> type[TunablesForm]:
    """A form over ``keys`` with values and overrides taken from a snapshot document.

    Raises UnknownKey for a key outside the catalogue. Duplicate keys are kept once, in first position.
    """
    catalogue = catalogue or get_catalogue()
    tunables: dict[str, Tunable] = {}
    values: dict[str, Any] = {}
    for key in dict.fromkeys(keys):
        tunables[key] = catalogue.get(key)
        group, _, name = key.partition(".")
        values[key] = document["groups"][group][name]
    overridden = [key for key in document["overridden"] if key in tunables]
    return form_class(tunables, values, overridden, document["version"], reason=reason)
