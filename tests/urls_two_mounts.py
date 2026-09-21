"""The API mounted twice, to prove the index links to the mount that served it."""

from django.urls import include, path

urlpatterns = [
    path("api/tunables/", include("tunables.api.urls")),
    path("internal/tunables/", include(("tunables.api.urls", "tunables"), namespace="internal")),
]
