from collections.abc import Collection, Mapping
from typing import Any

from django import forms

from tunables.catalogue import Group
from tunables.changes import Change


class GroupForm(forms.Form):
    """Base for the per-group form built by build_group_form. Tunable fields are added dynamically."""

    reason = forms.CharField(label="Reason", max_length=1000, widget=forms.Textarea(attrs={"rows": 2}))
    expected_version = forms.IntegerField(widget=forms.HiddenInput)

    group: Group
    overridden: Collection[str]

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        for tunable in self.group.tunables:
            if tunable.name in self.errors or cleaned.get(f"reset_{tunable.name}"):
                continue
            if cleaned.get(tunable.name) is None:
                self.add_error(tunable.name, "Enter a value or tick reset to default.")
        return cleaned

    def changes(self) -> list[Change]:
        """One change per tunable: a reset when its box is ticked, else the cleaned value."""
        changes = []
        for tunable in self.group.tunables:
            key = f"{self.group.name}.{tunable.name}"
            if self.cleaned_data.get(f"reset_{tunable.name}"):
                changes.append(Change(key, reset=True))
            else:
                changes.append(Change(key, tunable.type.to_json(self.cleaned_data[tunable.name])))
        return changes


def build_group_form(
    group: Group, values: Mapping[str, Any], overridden: Collection[str], version: int
) -> type[GroupForm]:
    """A GroupForm subclass with one field per tunable, initial values, and reset boxes for overrides."""
    attrs: dict[str, Any] = {"group": group, "overridden": frozenset(overridden)}
    for tunable in group.tunables:
        parts = [str(tunable.description), f"({tunable.unit})" if tunable.unit else ""]
        attrs[tunable.name] = tunable.type.form_field(
            label=str(tunable.title) or tunable.name,
            help_text=" ".join(part for part in parts if part),
            initial=values[tunable.name],
            required=False,
        )
        if tunable.name in overridden:
            attrs[f"reset_{tunable.name}"] = forms.BooleanField(label="Reset to default", required=False)
    attrs["expected_version"] = forms.IntegerField(widget=forms.HiddenInput, initial=version)
    return type(f"{group.name.title()}GroupForm", (GroupForm,), attrs)
