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

    def changes(self) -> list[Change]:
        """One change per tunable: a reset when its box is ticked, else the cleaned value."""
        raise NotImplementedError


def build_group_form(
    group: Group, values: Mapping[str, Any], overridden: Collection[str], version: int
) -> type[GroupForm]:
    """A GroupForm subclass with one field per tunable, initial values, and reset boxes for overrides."""
    raise NotImplementedError
