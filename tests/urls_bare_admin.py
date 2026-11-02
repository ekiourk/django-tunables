"""An admin site without the tunables admin, next to the package's own endpoint."""

from django.contrib.admin import AdminSite
from django.urls import include, path

bare = AdminSite(name="bare")

urlpatterns = [
    path("bare/", bare.urls),
    path("tunables/", include("tunables.urls")),
]
