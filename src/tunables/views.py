"""The view the embedded panel posts to."""

from django.contrib import messages
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views import View

from tunables.access import check_keys_editable
from tunables.changes import Actor
from tunables.errors import GroupNotEditable, UnknownKey
from tunables.models import ChangeSource, State
from tunables.panel import ERRORS_TEMPLATE, PanelConfig, build_panel, default_reason, page_path
from tunables.services import latest_snapshot
from tunables.sync import OUT_OF_SYNC_MESSAGE, is_current


def safe_next(request: HttpRequest) -> str:
    """The posted ``next`` when it points at this host, else the referer, else the site root."""
    for candidate in (request.POST.get("next", ""), request.headers.get("Referer", "")):
        if candidate and url_has_allowed_host_and_scheme(
            candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return candidate
    return "/"


def wants_partial(request: HttpRequest) -> bool:
    """HTMX and other in-page clients get the panel back instead of a redirect."""
    return "HX-Request" in request.headers or bool(request.POST.get("partial"))


class PanelSave(View):
    """Apply the changes of one embedded panel as a change set with source ``panel``."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest) -> HttpResponse:
        if not request.user.has_perm("tunables.add_changeset"):
            raise PermissionDenied
        try:
            config = PanelConfig.from_token(request.POST.get("panel", ""))
        except signing.BadSignature:
            return HttpResponseBadRequest("the panel token is missing or was altered")
        if not config.keys:
            return HttpResponseBadRequest("the panel covers no key")
        try:
            check_keys_editable(request, config.keys)
        except GroupNotEditable as refused:
            raise PermissionDenied(str(refused)) from refused
        next_url = safe_next(request)
        partial = wants_partial(request)
        state = State.objects.filter(pk=1).first()
        try:
            if not is_current(state):
                if partial:
                    panel = build_panel(request, config, next_url=next_url, mode="read", synced=False)
                    return render(request, config.template, {"panel": panel})
                messages.error(request, str(OUT_OF_SYNC_MESSAGE), fail_silently=True)
                return HttpResponseRedirect(next_url)
            assert state is not None
            document = latest_snapshot(state.current_version).document
            panel = build_panel(request, config, next_url=next_url, data=request.POST, mode="edit", document=document)
        except UnknownKey as unknown:
            raise Http404(str(unknown)) from unknown
        form = panel.form
        assert form is not None
        if form.is_valid():
            result = form.save(
                actor=Actor(request.user.get_username(), "verified"),
                source=ChangeSource.PANEL,
                reason=form.cleaned_data.get("reason") or default_reason(next_url),
                metadata={"page": page_path(next_url)},
            )
            if result is not None:
                if partial:
                    panel = build_panel(
                        request, config, next_url=next_url, mode="edit", document=result.snapshot.document
                    )
                    panel.saved_version = result.version
                    return render(request, config.template, {"panel": panel})
                messages.success(
                    request, _("Saved version %(version)s.") % {"version": result.version}, fail_silently=True
                )
                return HttpResponseRedirect(next_url)
            if form.conflict is not None:
                panel.form = form.rebound()
                panel.notice = panel.form.conflict_text()
        return render(request, config.template if partial else ERRORS_TEMPLATE, {"panel": panel})
