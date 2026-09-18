from django.urls import path

from tunables.api import views, writes

app_name = "tunables"

urlpatterns = [
    path("categories/", views.CategoryList.as_view(), name="categories"),
    path("groups/", views.GroupList.as_view(), name="groups"),
    path("groups/<str:group>/", views.GroupDetail.as_view(), name="group"),
    path("groups/<str:group>/schema/", views.GroupSchema.as_view(), name="group-schema"),
    path("groups/<str:group>/values/", views.GroupValues.as_view(), name="group-values"),
    path("schema/", views.SchemaList.as_view(), name="schema"),
    path("definitions/", views.DefinitionList.as_view(), name="definitions"),
    path("tags/", views.TagList.as_view(), name="tags"),
    path("tags/<str:name>/", views.TagDetail.as_view(), name="tag"),
    path("definitions/<str:key>/tags/", writes.DefinitionTags.as_view(), name="definition-tags"),
    path("values/", views.Values.as_view(), name="values"),
    path("changesets/", views.ChangeSetList.as_view(), name="changesets"),
    path("changesets/<int:version>/", views.ChangeSetDetail.as_view(), name="changeset"),
    path("snapshots/latest/", views.LatestSnapshot.as_view(), name="snapshot-latest"),
    path("snapshots/schema/", views.SnapshotSchema.as_view(), name="snapshot-schema"),
    path("snapshots/<int:version>/", views.SnapshotDetail.as_view(), name="snapshot"),
    path("export/", views.Export.as_view(), name="export"),
    path("diff/", views.Diff.as_view(), name="diff"),
    path("status/", views.Status.as_view(), name="status"),
    path("validate/", writes.Validate.as_view(), name="validate"),
    path("rollback/", writes.Rollback.as_view(), name="rollback"),
    path("import/", writes.Import.as_view(), name="import"),
]
