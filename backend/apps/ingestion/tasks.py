"""Celery tasks for the ingestion pipeline (DATA_INGESTION §4.3).

:func:`ingest_job` runs a registered pipeline under an ``ImportJob``. Permanent
failures are handled *inside* the pipeline (:class:`PipelineError` → DLQ row +
``failed`` job + ``ingestion.failed`` event), so only **transient** failures
propagate here, where they are retried with exponential backoff + jitter
(bounded at :data:`MAX_RETRIES`). ``autoretry_for=()`` disables the global
annotation so the retry budget and countdown are explicit and testable.
"""

from __future__ import annotations

import random
from typing import Any

from apps.ingestion.services.pipeline import PipelineReport, run_pipeline
from config.celery import app

#: Bounded retries for transient failures (DATA_INGESTION §4.3: "e.g. 8").
MAX_RETRIES = 8
#: Base countdown (seconds) for the exponential backoff.
_BACKOFF_BASE = 5


def backoff_seconds(retries: int) -> int:
    """Exponential backoff with jitter: ``base * 2**retries + jitter``."""
    base: int = _BACKOFF_BASE * (2**retries)
    jitter: int = random.randint(0, _BACKOFF_BASE - 1)
    return base + jitter


@app.task(bind=True, name="ingestion.ingest_job", max_retries=MAX_RETRIES, autoretry_for=())  # type: ignore[untyped-decorator]
def ingest_job(
    self: Any,
    pipeline_name: str,
    factory_args: dict[str, Any],
    created_by: Any = None,
) -> PipelineReport:
    """Run ``pipeline_name`` with ``factory_args``; retry transient failures."""
    try:
        return run_pipeline(
            pipeline_name=pipeline_name, factory_args=factory_args, created_by=created_by
        )
    except Exception as exc:
        # Transient: exponential backoff + jitter, bounded. Permanent pipeline
        # failures never reach here (run_pipeline returns a failed report).
        raise self.retry(exc=exc, countdown=backoff_seconds(self.request.retries)) from exc


__all__ = ["MAX_RETRIES", "backoff_seconds", "ingest_job"]
