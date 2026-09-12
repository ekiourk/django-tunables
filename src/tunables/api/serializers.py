from rest_framework import serializers

from tunables.models import ChangeItem, ChangeSet


class ChangeItemSerializer(serializers.ModelSerializer[ChangeItem]):
    class Meta:
        model = ChangeItem
        fields = ["key", "old_value", "new_value", "reset"]


class ChangeSetSerializer(serializers.ModelSerializer[ChangeSet]):
    item_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = ChangeSet
        fields = [
            "version",
            "created_at",
            "actor",
            "actor_source",
            "client",
            "reason",
            "source",
            "restores_version",
            "request_id",
            "catalogue_version",
            "metadata",
            "item_count",
        ]


class ChangeSetDetailSerializer(ChangeSetSerializer):
    items = ChangeItemSerializer(many=True, read_only=True)

    class Meta(ChangeSetSerializer.Meta):
        fields = [*ChangeSetSerializer.Meta.fields, "items"]
