"""``ImportJob`` lifecycle and the ``IngestionError`` DLQ (DATA_INGESTION §3.4/§4).

The pipeline drives jobs through a small state machine:

* **created** — the idempotency key was unused; a fresh job row is written
  already ``running``;
* **resumed** — a previous run left the job ``queued``/``running`` (crash, or
  an ops reset); the job is reopened so the import can continue from the last
  committed ``SyncCheckpoint`` (DATA_INGESTION §4.7 restart safety);
* **skipped** — the same key already reached a terminal state (``succeeded``,
  ``partial``, ``failed``); re-running is a no-op, and the historical row is
  left untouched.

:func:`record_error` is the only way DLQ rows are written. It redacts the
payload (:mod:`ingestion.services.redact`), stamps the job/provider/stage and
carries the retry budget (``attempt``/``next_retry_at``). The DLQ row commits
in the same transaction as the job's terminal status, so the two never drift.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import IntegrityError, models, transaction
from django.utils import timezone

from apps.ingestion.models import ImportJob, IngestionError
from apps.ingestion.services.redact import redact

#: Job states that mean "a run may still happen" (crash-resumable).
_RESUMABLE = {"queued", "running"}
#: Job states that are terminal (a re-run with the same key is a no-op).
_TERMINAL = {"succeeded", "partial", "failed", "skipped"}


def create_or_resume_job(
    provider: Any,
    *,
    job_type: str,
    scope: dict[str, Any] | None,
    idempotency_key: str,
    created_by: Any = None,
) -> tuple[ImportJob, str]:
    """Return ``(job, action)`` for one idempotency key.

    ``action`` is ``"created"``, ``"resumed"`` or ``"skipped"``. The job is
    left in ``running`` state with ``started_at`` refreshed for created/resumed
    runs; skipped jobs are returned untouched. A concurrent duplicate create is
    folded onto the winner of the unique ``ux_import_job_idempotency`` race.
    """
    existing = ImportJob.objects.filter(idempotency_key=idempotency_key).first()
    if existing is None:
        try:
            with transaction.atomic():
                job = ImportJob.objects.create(
                    provider=provider,
                    job_type=job_type,
                    scope=scope,
                    idempotency_key=idempotency_key,
                    created_by=created_by,
                    status="running",
                    started_at=timezone.now(),
                )
            return job, "created"
        except IntegrityError:
            # Lost the race: another run committed the same key first.
            existing = ImportJob.objects.get(idempotency_key=idempotency_key)
    if existing.status in _RESUMABLE:
        existing.status = "running"
        existing.started_at = timezone.now()
        existing.save(update_fields=["status", "started_at"])
        return existing, "resumed"
    return existing, "skipped"


def mark_succeeded(job: ImportJob, *, read: int, written: int, skipped: int) -> None:
    """Terminal success with the run's counters."""
    job.status = "succeeded"
    job.records_read = read
    job.records_written = written
    job.records_skipped = skipped
    job.finished_at = timezone.now()
    job.error = None
    job.save(
        update_fields=[
            "status",
            "records_read",
            "records_written",
            "records_skipped",
            "finished_at",
            "error",
        ]
    )


def mark_partial(
    job: ImportJob, *, read: int, written: int, skipped: int, error: str | None = None
) -> None:
    """Terminal success with some rows rejected to the DLQ (status ``partial``)."""
    job.status = "partial"
    job.records_read = read
    job.records_written = written
    job.records_skipped = skipped
    job.finished_at = timezone.now()
    job.error = error
    job.save(
        update_fields=[
            "status",
            "records_read",
            "records_written",
            "records_skipped",
            "finished_at",
            "error",
        ]
    )


def mark_failed(job: ImportJob, *, error: str, error_class: str | None = None) -> None:
    """Terminal failure (a DLQ row is written alongside by the pipeline)."""
    job.status = "failed"
    job.error = f"{error_class}: {error}" if error_class else error
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "error", "finished_at"])


def record_error(
    job: ImportJob | None,
    *,
    stage: str,
    payload: Any = None,
    error_class: str | None = None,
    error_detail: str | None = None,
    attempt: int = 1,
    next_retry_at: datetime | None = None,
) -> IngestionError:
    """Write one ``IngestionError`` DLQ row (payload redacted; audit-safe).

    ``provider_id`` falls back to the job's provider so rows keep a provider
    stamp even when the job reference is absent.
    """
    return IngestionError.objects.create(
        import_job=job,
        provider_id=job.provider_id if job is not None else None,
        stage=stage,
        payload=redact(payload) if payload is not None else None,
        error_class=error_class,
        error_detail=error_detail,
        attempt=attempt,
        next_retry_at=next_retry_at,
    )


def open_errors(*, limit: int = 100) -> models.QuerySet[IngestionError]:
    """Unresolved DLQ rows, oldest first (ops dashboard / admin surface)."""
    return IngestionError.objects.filter(resolved=False).order_by("created_at")[:limit]


__all__ = [
    "create_or_resume_job",
    "mark_failed",
    "mark_partial",
    "mark_succeeded",
    "open_errors",
    "record_error",
]
