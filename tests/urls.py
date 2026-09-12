from django.urls import include, path

urlpatterns = [path("api/tunables/", include("tunables.api.urls"))]
