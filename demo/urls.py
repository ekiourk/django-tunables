from django.contrib import admin
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import include, path
from django.views.generic import TemplateView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/tunables/", include("tunables.api.urls")),
    path("tunables/", include("tunables.urls")),
    path("login/", LoginView.as_view()),
    path("logout/", LogoutView.as_view(next_page="/")),
    path("shop/settings/", TemplateView.as_view(template_name="demo/settings.html")),
    path("", TemplateView.as_view(template_name="demo/index.html")),
]
