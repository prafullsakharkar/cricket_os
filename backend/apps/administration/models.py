"""The AuditLog entity (append-only admin-action trail).

Columns and constraint/index names match CRICKET_DATABASE_SCHEMA §3.8.
``entity_id`` is polymorphic (plain UUID). Rows are written by the synchronous
audit subscriber in ``administration/handlers.py`` so the audit and its change
commit or roll back together.
"""

from __future__ import annotations

from django.db import models

from apps.core.models import UUIDPrimaryKeyModel


class AuditLog(UUIDPrimaryKeyModel):
    actor_id = models.UUIDField(null=True, blank=True)
    actor_label = models.TextField(null=True, blank=True)
    action = models.TextField()
    entity_type = models.TextField(null=True, blank=True)
    entity_id = models.UUIDField(null=True, blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "audit_log"
        indexes = [
            models.Index(fields=["entity_type", "entity_id"], name="ix_audit_entity"),
            models.Index(fields=["-created_at"], name="ix_audit_created"),
        ]

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} {self.entity_type}:{self.entity_id}"


__all__ = ["AuditLog"]
