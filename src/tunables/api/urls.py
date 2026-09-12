from django.urls import path

from tunables.api import views

app_name = "tunables"

urlpatterns = [
    path("groups/", views.GroupList.as_view(), name="groups"),
    path("groups/<str:group>/", views.GroupDetail.as_view(), name="group"),
    path("groups/<str:group>/schema/", views.GroupSchema.as_view(), name="group-schema"),
    path("groups/<str:group>/values/", views.GroupValues.as_view(), name="group-values"),
    path("schema/", views.SchemaList.as_view(), name="schema"),
    path("definitions/", views.DefinitionList.as_view(), name="definitions"),
    path("values/", views.Values.as_view(), name="values"),
    path("changesets/", views.ChangeSetList.as_view(), name="changesets"),
    path("changesets/<int:version>/", views.ChangeSetDetail.as_view(), name="changeset"),
    path("snapshots/latest/", views.LatestSnapshot.as_view(), name="snapshot-latest"),
    path("snapshots/<int:version>/", views.SnapshotDetail.as_view(), name="snapshot"),
    path("export/", views.Export.as_view(), name="export"),
    path("validate/", views.Validate.as_view(), name="validate"),
    path("rollback/", views.Rollback.as_view(), name="rollback"),
    path("import/", views.Import.as_view(), name="import"),
]
