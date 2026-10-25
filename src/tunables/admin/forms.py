from django import forms
from django.utils.translation import gettext_lazy as _

from tunables.identifiers import TAG


class DefinitionTagsForm(forms.Form):
    """The manual tags of one definition as comma-separated names."""

    tags = forms.CharField(
        label=_("Tags"),
        required=False,
        help_text=_("Comma-separated. Names use lowercase letters, digits, hyphens and underscores."),
    )

    def clean_tags(self) -> list[str]:
        names = [name.strip() for name in self.cleaned_data["tags"].split(",") if name.strip()]
        bad = [name for name in names if not TAG.match(name)]
        if bad:
            raise forms.ValidationError(_("Invalid tag names: %(names)s") % {"names": ", ".join(bad)})
        return names
