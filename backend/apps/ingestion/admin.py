"""Admin for the ingestion pipeline: ``ImportJob`` history + the DLQ.

``IngestionError`` rows are the dead-letter queue (CRICKET_DATA_INGESTION §4):
permanent failures land here with a **redacted** payload and the failing
stage/class. The admin is the ops dashboard:

* **mark resolved** — triage a DLQ row once the underlying problem is fixed
  (the partial-unique index ``ix_ingestion_error_open`` tracks open rows);
* **reopen** — pull a row back into the open set (e.g. a wrong triage, or a
  transient fix that needs a manual retry watch).

Jobs are created by the pipeline, not by hand, so ``ImportJob`` is
display-only (no add/delete).
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest

from apps.ingestion.models import ImportJob, IngestionError


@admin.action(description="Mark selected errors as resolved (triaged)")
def mark_resolved(
    modeladmin: Any, request: HttpRequest, queryset: QuerySet[IngestionError]
) -> None:
    queryset.update(resolved=True)


@admin.action(description="Reopen selected errors (back into the open DLQ)")
def reopen_errors(
    modeladmin: Any, request: HttpRequest, queryset: QuerySet[IngestionError]
) -> None:
    queryset.update(resolved=False, next_retry_at=None)


@admin.register(ImportJob)
class ImportJobAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "job_type",
        "status",
        "provider",
        "idempotency_key",
        "records_read",
        "records_written",
        "started_at",
        "finished_at",
    )
    list_filter = ("status", "job_type", "provider")
    search_fields = ("idempotency_key", "scope", "error")
    readonly_fields = (
        "provider",
        "job_type",
        "scope",
        "status",
        "idempotency_key",
        "started_at",
        "finished_at",
        "records_read",
        "records_written",
        "records_skipped",
        "error",
        "created_by",
    )

    def has_add_permission(self, request: Any) -> bool:
        return False

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        return False


@admin.register(IngestionError)
class IngestionErrorAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """The dead-letter queue: unresolved rows surface first."""

    list_display = (
        "created_at",
        "stage",
        "error_class",
        "import_job",
        "provider_id",
        "attempt",
        "next_retry_at",
        "resolved",
    )
    list_filter = ("stage", "error_class", "resolved", "provider_id")
    search_fields = ("error_class", "error_detail", "payload")
    # DLQ ops: triage and reopen.
    actions = (mark_resolved, reopen_errors)
    readonly_fields = (
        "import_job",
        "provider_id",
        "stage",
        "payload",
        "error_class",
        "error_detail",
        "attempt",
        "next_retry_at",
        "resolved",
        "created_at",
    )

    def has_add_permission(self, request: Any) -> bool:
        return False

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        return False


__all__ = [
    "ImportJobAdmin",
    "IngestionErrorAdmin",
    "mark_resolved",
    "reopen_errors",
]
