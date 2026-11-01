"""The admin mounted twice, to prove the breadcrumbs link to the mount that served the page."""

from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("ops/", (admin.site.get_urls(), "admin", "ops")),
]
