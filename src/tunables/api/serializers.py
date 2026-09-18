from datetime import UTC
from typing import Any

from rest_framework import serializers

from tunables.models import ChangeItem, ChangeSet


class ChangeItemSerializer(serializers.ModelSerializer[ChangeItem]):
    class Meta:
        model = ChangeItem
        fields = ["key", "old_value", "new_value", "reset"]


class ChangeSetSerializer(serializers.ModelSerializer[ChangeSet]):
    created_at = serializers.DateTimeField(default_timezone=UTC, read_only=True)
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


class ChangeSerializer(serializers.Serializer[dict[str, Any]]):
    key = serializers.CharField()
    value = serializers.JSONField(required=False, allow_null=True)
    reset = serializers.BooleanField(default=False)


class ChangesRequestSerializer(serializers.Serializer[dict[str, Any]]):
    changes = serializers.ListField(child=ChangeSerializer())
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    dry_run = serializers.BooleanField(default=False)
    metadata = serializers.DictField(required=False, default=dict)


class RollbackRequestSerializer(serializers.Serializer[dict[str, Any]]):
    to_version = serializers.IntegerField(min_value=0)
    reason = serializers.CharField(required=False, allow_blank=True, default="")
