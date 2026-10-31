"""Admin for the audit trail (append-only: no add/delete)."""

from __future__ import annotations

from typing import Any

from django.contrib import admin

from apps.administration.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("created_at", "action", "entity_type", "entity_id", "actor_label")
    list_filter = ("action", "entity_type")
    search_fields = ("entity_id", "actor_label")
    readonly_fields = (
        "actor_id",
        "actor_label",
        "action",
        "entity_type",
        "entity_id",
        "before",
        "after",
        "ip",
        "created_at",
    )

    def has_add_permission(self, request: Any) -> bool:
        return False

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        return False


__all__ = ["AuditLogAdmin"]
