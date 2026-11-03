"""Admin for synchronization: ``SyncCheckpoint`` + the ``DataCorrection`` audit.

Checkpoints are advanced by the ingestion pipeline **after** the surrounding
transaction commits (CRICKET_DATA_INGESTION §3.5) — the admin is display-only
so a human can inspect resume positions. ``DataCorrection`` rows are the
append-only audit trail of the delivery write path's supersede operations;
they are never edited or deleted.
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin

from apps.synchronization.models import DataCorrection, ReconciliationJob, SyncCheckpoint


@admin.register(SyncCheckpoint)
class SyncCheckpointAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "provider",
        "scope_key",
        "cursor",
        "last_delivery_seq",
        "last_run_at",
        "last_provider_updated_at",
    )
    search_fields = ("scope_key", "cursor")
    list_filter = ("provider",)
    readonly_fields = (
        "provider",
        "scope_key",
        "cursor",
        "last_delivery_seq",
        "last_run_at",
        "last_provider_updated_at",
    )

    def has_add_permission(self, request: Any) -> bool:
        return False

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        return False


@admin.register(DataCorrection)
class DataCorrectionAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "applied_at",
        "entity_type",
        "entity_id",
        "provider",
        "source_revision",
        "target_revision",
        "reason",
    )
    search_fields = ("entity_id", "provider_entity_id", "reason", "field_path")
    list_filter = ("entity_type", "provider")
    readonly_fields = (
        "provider",
        "entity_type",
        "entity_id",
        "provider_entity_id",
        "field_path",
        "old_value",
        "new_value",
        "reason",
        "applied_by",
        "applied_at",
        "source_revision",
        "target_revision",
    )

    def has_add_permission(self, request: Any) -> bool:
        return False

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        return False


@admin.action(description="Mark selected jobs resolved", permissions=("change",))
def resolve_jobs(modeladmin: Any, request: Any, queryset: Any) -> None:
    from apps.synchronization.services.reconciliation import resolve_job

    for job in queryset:
        resolve_job(job)


@admin.action(description="Mark selected jobs ignored", permissions=("change",))
def ignore_jobs(modeladmin: Any, request: Any, queryset: Any) -> None:
    from apps.synchronization.services.reconciliation import resolve_job

    for job in queryset:
        resolve_job(job, status=ReconciliationJob.STATUS_IGNORED)


@admin.register(ReconciliationJob)
class ReconciliationJobAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Detected divergences; resolution is a manual steward action."""

    list_display = (
        "created_at",
        "scope_key",
        "status",
        "resolved_at",
    )
    search_fields = ("scope_key",)
    list_filter = ("status",)
    readonly_fields = (
        "scope_key",
        "expected_state",
        "observed_state",
        "differences",
        "created_at",
        "resolved_at",
    )
    actions = (resolve_jobs, ignore_jobs)

    def has_add_permission(self, request: Any) -> bool:
        return False

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        return False


__all__ = ["DataCorrectionAdmin", "ReconciliationJobAdmin", "SyncCheckpointAdmin"]
