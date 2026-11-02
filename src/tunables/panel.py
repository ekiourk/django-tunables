"""The embedded panel: a form or a read-only list over chosen tunables, rendered inside a host page.

The template tag and the save view both build a PanelState and render it with the panel template,
so the page shown after a failed save is the same panel the host page showed. The panel's
configuration, which keys it covers and how it treats the reason, travels to the save view as a
signed token, so a browser cannot widen a panel or drop a reason the page author required.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast
from urllib.parse import urlsplit

from django.core import signing
from django.http import HttpRequest
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext as _

from tunables.access import check_keys_editable
from tunables.errors import GroupNotEditable
from tunables.forms import REASON_MODES, ReasonMode, Row, TunablesForm, build_form
from tunables.models import State
from tunables.registry import get_catalogue
from tunables.search import require_keys
from tunables.services import latest_snapshot, live_document
from tunables.sync import OUT_OF_SYNC_MESSAGE, is_current

Mode = Literal["edit", "read", "hidden"]
Only = Literal["edit"]

PANEL_TEMPLATE = "tunables/panel.html"
ERRORS_TEMPLATE = "tunables/panel_errors.html"
TOKEN_SALT = "tunables.panel"


@dataclass(frozen=True)
class PanelConfig:
    """What the page author chose. Signed into the form so the save view can trust it."""

    keys: tuple[str, ...]
    reason: ReasonMode = "required"
    title: str = ""
    id: str = "tunables-panel"
    template: str = PANEL_TEMPLATE
    only: Only | None = None

    def __post_init__(self) -> None:
        if self.reason not in REASON_MODES:
            raise ValueError(f"reason must be one of {REASON_MODES}, not {self.reason!r}")
        if self.only not in (None, "edit"):
            raise ValueError(f"only must be 'edit' when given, not {self.only!r}")
        object.__setattr__(self, "keys", tuple(dict.fromkeys(self.keys)))

    def token(self) -> str:
        fields = [list(self.keys), self.reason, self.title, self.id, self.template]
        if self.only is not None:  # without it the token stays readable by releases that predate the option
            fields.append(self.only)
        return signing.dumps(fields, salt=TOKEN_SALT, compress=True)

    @classmethod
    def from_token(cls, token: str) -> "PanelConfig":
        """Raises signing.BadSignature for a missing, altered or foreign token."""
        try:
            keys, reason, title, element_id, template, *only = signing.loads(token, salt=TOKEN_SALT)
            return cls(tuple(keys), cast(ReasonMode, reason), title, element_id, template, *only)
        except (TypeError, ValueError) as bad:
            raise signing.BadSignature(str(bad)) from bad


@dataclass
class PanelState:
    """Everything the panel template needs. ``form`` is None in hidden mode and for an edit-only notice."""

    config: PanelConfig
    mode: Mode
    next_url: str
    form: TunablesForm | None = None
    notice: str = ""
    saved_version: int | None = None
    action_url: str = ""

    @property
    def token(self) -> str:
        return self.config.token()

    @property
    def rows(self) -> list[Row]:
        return self.form.rows() if self.form is not None else []


def build_panel(
    request: HttpRequest,
    config: PanelConfig,
    *,
    next_url: str = "",
    data: Mapping[str, object] | None = None,
    mode: Mode | None = None,
    synced: bool = True,
    document: Mapping[str, object] | None = None,
) -> PanelState:
    """Build the panel for ``config``.

    Left to decide, the panel is in edit mode for a writer whose groups are all editable on a synced
    catalogue, read mode for a viewer or when out of sync, and hidden otherwise, without touching the
    database for a hidden panel. With ``only="edit"`` read mode becomes hidden, except for a writer on
    an out-of-sync catalogue, who gets the notice without values. A caller that already decided passes
    ``mode`` and ``synced``, and ``document`` when it already holds the one to show. Unknown keys raise
    UnknownKey, for every user.
    """
    require_keys(get_catalogue(), config.keys)
    next_url = next_url or request.get_full_path()
    version = None
    if mode is None:
        user = request.user
        can_write = user.has_perm("tunables.add_changeset")
        if not can_write and (config.only == "edit" or not user.has_perm("tunables.view_tunabledefinition")):
            return PanelState(config, "hidden", next_url)
        state = State.objects.filter(pk=1).first()
        synced = is_current(state)
        mode = "edit" if can_write and synced and _editable(request, config.keys) else "read"
        if synced:
            assert state is not None
            version = state.current_version
    notice = "" if synced else str(OUT_OF_SYNC_MESSAGE)
    if mode == "read" and config.only == "edit":
        return PanelState(config, "read" if notice else "hidden", next_url, notice=notice)
    if document is None:
        document = latest_snapshot(version).document if synced else live_document()
    form_class = build_form(config.keys, document, reason=config.reason)
    form = form_class(data) if data is not None else form_class()
    action_url = save_url(request) if mode == "edit" else ""
    return PanelState(config, mode, next_url, form=form, notice=notice, action_url=action_url)


def save_url(request: HttpRequest) -> str:
    """The save URL of the admin site that served ``request`` if it has the tunables admin, else the package's."""
    match = request.resolver_match
    if match is not None and match.app_name == "admin":
        try:
            return reverse("admin:tunables_panel_save", current_app=match.namespace)
        except NoReverseMatch:
            pass
    return reverse("tunables_ui:panel-save")


def _editable(request: HttpRequest, keys: tuple[str, ...]) -> bool:
    try:
        check_keys_editable(request, keys)
    except GroupNotEditable:
        return False
    return True


def page_path(url: str) -> str:
    """The path of the host page, as recorded on a change set."""
    return urlsplit(url).path or "/"


def default_reason(url: str) -> str:
    """The reason stored when the panel's reason field was hidden or left empty."""
    return _("Changed from %(page)s") % {"page": page_path(url)}
