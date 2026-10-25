"""Pages and endpoints of the embedded UI. Mount with ``path("tunables/", include("tunables.urls"))``."""

from django.urls import path

from tunables.views import PanelSave

app_name = "tunables_ui"

urlpatterns = [
    path("panel/save/", PanelSave.as_view(), name="panel-save"),
]
