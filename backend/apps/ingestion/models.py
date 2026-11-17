"""Ingestion tracking: ``ImportJob`` and the ``IngestionError`` DLQ.

``ImportJob.idempotency_key`` is unique: re-running the same job is a no-op.
``IngestionError`` rows are the DLQ (CRICKET_DATA_INGESTION §4): they record
the failing stage, the error class, and a **redacted** payload (no secrets or
credential material ever — redaction is enforced by
``ingestion.services.redact`` before a row is written). ``resolved`` marks the
row triaged; ``attempt``/``next_retry_at`` track the retry budget.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from apps.core.models import UUIDPrimaryKeyModel

#: Import lifecycle states (DATA_INGESTION §3.4).
JOB_STATUSES = (
    "queued",
    "running",
    "succeeded",
    "failed",
    "partial",
    "skipped",
)


class ImportJob(UUIDPrimaryKeyModel):
    provider = models.ForeignKey(
        "providers.DataProvider",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="import_jobs",
        db_index=False,
    )
    job_type = models.TextField()
    scope = models.JSONField(null=True, blank=True)
    status = models.TextField(default="queued")
    # db_index=False: unique=True already creates the unique index; this stops
    # Django adding a *_pattern_ops LIKE-aid index the reference DDL lacks.
    idempotency_key = models.TextField(null=True, blank=True, unique=True, db_index=False)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    records_read = models.IntegerField(null=True, blank=True)
    records_written = models.IntegerField(null=True, blank=True)
    records_skipped = models.IntegerField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)
    created_by = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "import_job"

    def __str__(self) -> str:
        return f"{self.job_type}:{self.status}"


class IngestionError(UUIDPrimaryKeyModel):
    import_job = models.ForeignKey(
        "ingestion.ImportJob",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="errors",
        db_index=False,
    )
    provider_id = models.UUIDField(null=True, blank=True)
    stage = models.TextField()
    payload = models.JSONField(null=True, blank=True)
    error_class = models.TextField(null=True, blank=True)
    error_detail = models.TextField(null=True, blank=True)
    attempt = models.IntegerField(default=1)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ingestion_error"
        indexes = [
            models.Index(
                fields=["created_at"],
                condition=models.Q(resolved=False),
                name="ix_ingestion_error_open",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.stage}:{self.error_class}"
