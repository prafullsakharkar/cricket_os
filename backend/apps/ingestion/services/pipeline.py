"""The ingestion pipeline: fetch → validate → resolve → persist (DATA_INGESTION §3).

:func:`run_pipeline` drives a sequence of **stages** under one
``ImportJob``:

* **idempotent** — ``ImportJob.idempotency_key`` is unique; a terminal job
  with the same key is re-run as a no-op (``skipped``) and a crashed
  ``queued``/``running`` job is **resumed** (crash safety, DATA_INGESTION
  §4.7);
* **atomic** — the job row, stage writes, DLQ rows and the terminal status
  commit in one transaction; a transient stage exception rolls everything back
  and propagates to the caller (the Celery task retries with backoff);
* **DLQ on permanent failure** — a stage raising :class:`PipelineError` gets a
  redacted ``IngestionError`` row plus a ``failed`` job in the same
  transaction, and ``ingestion.failed`` is published after commit;
* **checkpoint after commit** — when a spec sets ``checkpoint_scope``, the
  ``SyncCheckpoint`` is advanced via ``transaction.on_commit`` so it is never
  ahead of committed data (DATA_INGESTION §3.5);
* **events after commit** — ``ingestion.completed`` / ``ingestion.failed`` are
  published with the job counters.

Adapters (Groups 5–6) register a :data:`PipelineFactory` per pipeline name
(:func:`register_pipeline`) and the management commands / Celery tasks dispatch
through :func:`run_pipeline`. A stage returns a :class:`StageResult`; the
pipeline accumulates its counters and treats ``errors > 0`` as a ``partial``
job.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.db import transaction

from apps.core import events as core_events
from apps.core.errors import CricketError
from apps.ingestion.models import ImportJob
from apps.ingestion.services.jobs import (
    create_or_resume_job,
    mark_failed,
    mark_partial,
    mark_succeeded,
    record_error,
)
from apps.providers.models import DataProvider
from apps.synchronization.services.sync import advance_checkpoint, get_checkpoint


class PipelineNotRegistered(CricketError):
    """Raised when ``run_pipeline`` cannot find a pipeline for a name."""

    code = "unprocessable"
    status_code = 422


class PipelineError(Exception):
    """A permanent ingestion failure: DLQ row + ``failed`` job; no retry.

    ``payload`` is stored redacted on the DLQ row. Transient failures should
    simply raise anything else — the pipeline rolls back and the task retries.
    """

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        payload: Any = None,
        error_class: str | None = None,
        attempt: int = 1,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.payload = payload
        self.error_class = error_class
        self.attempt = attempt


@dataclass(frozen=True)
class StageResult:
    """What one pipeline stage produced; counters accumulate across stages."""

    read: int = 0
    written: int = 0
    skipped: int = 0
    errors: int = 0
    #: Optional checkpoint position reported by the stage (latest wins).
    last_delivery_seq: int | None = None
    last_provider_updated_at: datetime | None = None
    checkpoint_cursor: str | None = None


@dataclass
class IngestContext:
    """Per-run context handed to every stage (mutable counters)."""

    job: ImportJob
    provider: DataProvider
    scope: dict[str, Any]
    checkpoint: Any  # SyncCheckpoint | None (imported lazily to avoid cycles)
    read: int = 0
    written: int = 0
    skipped: int = 0
    errors: int = 0


@dataclass(frozen=True)
class PipelineSpec:
    """Everything :func:`run_pipeline` needs for one pipeline run."""

    provider_code: str
    job_type: str
    idempotency_key: str
    scope: dict[str, Any]
    stages: Sequence[Callable[[IngestContext], StageResult]]
    #: When set, a SyncCheckpoint is advanced (after commit) on success.
    checkpoint_scope: str | None = None


@dataclass(frozen=True)
class PipelineReport:
    """The outcome of a pipeline run (what the job row records)."""

    job_id: str
    idempotency_key: str
    action: str  # "created" | "resumed" | "skipped"
    status: str
    job_type: str
    provider_code: str
    read: int = 0
    written: int = 0
    skipped: int = 0
    errors: int = 0
    error: str | None = None
    error_class: str | None = None


#: name -> factory; adapters register here (cricsheet, summary_cards, …).
PipelineFactory = Callable[[dict[str, Any]], PipelineSpec]
_registry: dict[str, PipelineFactory] = {}


def register_pipeline(name: str, factory: PipelineFactory) -> None:
    """Register (or replace) the pipeline factory for ``name``."""
    _registry[name] = factory


def get_pipeline(name: str) -> PipelineFactory:
    """Return the registered pipeline factory or raise ``PipelineNotRegistered``."""
    try:
        return _registry[name]
    except KeyError as exc:
        raise PipelineNotRegistered(f"No pipeline registered for {name!r}") from exc


def list_pipelines() -> list[str]:
    """Sorted names of every registered pipeline."""
    return sorted(_registry)


def run_pipeline(
    *,
    pipeline_name: str,
    factory_args: dict[str, Any],
    created_by: Any = None,
) -> PipelineReport:
    """Resolve a registered pipeline and run it under an ``ImportJob``.

    ``factory_args`` are passed verbatim to the pipeline factory (a file path,
    a provider code, a match id, … — adapter-defined). The factory returns the
    full :class:`PipelineSpec` including the idempotency key.
    """
    spec = get_pipeline(pipeline_name)(factory_args)
    provider = DataProvider.objects.get(code=spec.provider_code)
    return _run_spec(spec, provider, created_by=created_by)


def run_pipeline_spec(
    spec: PipelineSpec,
    *,
    created_by: Any = None,
) -> PipelineReport:
    """Run an already-built spec (used by programmatic wrappers)."""
    provider = DataProvider.objects.get(code=spec.provider_code)
    return _run_spec(spec, provider, created_by=created_by)


def _run_spec(
    spec: PipelineSpec,
    provider: DataProvider,
    *,
    created_by: Any = None,
) -> PipelineReport:
    with transaction.atomic():
        job, action = create_or_resume_job(
            provider,
            job_type=spec.job_type,
            scope=spec.scope,
            idempotency_key=spec.idempotency_key,
            created_by=created_by,
        )
        if action == "skipped":
            return _report(job, spec, action, job.status)

        checkpoint = (
            get_checkpoint(provider, spec.checkpoint_scope) if spec.checkpoint_scope else None
        )
        ctx = IngestContext(
            job=job,
            provider=provider,
            scope=spec.scope,
            checkpoint=checkpoint,
        )

        # Latest checkpoint position reported by any stage (advanced on_commit).
        last_delivery_seq: int | None = None
        last_provider_updated_at: datetime | None = None
        checkpoint_cursor: str | None = None
        error: str | None = None
        error_class: str | None = None

        try:
            for stage in spec.stages:
                result = stage(ctx)
                ctx.read += result.read
                ctx.written += result.written
                ctx.skipped += result.skipped
                ctx.errors += result.errors
                if result.last_delivery_seq is not None:
                    last_delivery_seq = result.last_delivery_seq
                if result.last_provider_updated_at is not None:
                    last_provider_updated_at = result.last_provider_updated_at
                if result.checkpoint_cursor is not None:
                    checkpoint_cursor = result.checkpoint_cursor
        except PipelineError as exc:
            record_error(
                job,
                stage=exc.stage,
                payload=exc.payload,
                error_class=exc.error_class or type(exc).__name__,
                error_detail=str(exc),
                attempt=exc.attempt,
            )
            error = str(exc)
            error_class = exc.error_class or type(exc).__name__
            mark_failed(job, error=error, error_class=error_class)
            core_events.publish(
                core_events.TOPIC_INGESTION_FAILED,
                _event_payload(job, spec, "failed", ctx, error=error, error_class=error_class),
            )
            return _report(job, spec, action, "failed", ctx, error=error, error_class=error_class)

        status = "partial" if ctx.errors else "succeeded"
        if status == "partial":
            mark_partial(job, read=ctx.read, written=ctx.written, skipped=ctx.skipped)
        else:
            mark_succeeded(job, read=ctx.read, written=ctx.written, skipped=ctx.skipped)

        checkpoint_scope = spec.checkpoint_scope
        if checkpoint_scope:
            # Narrowed to ``str`` for the closure (mypy does not narrow through
            # lambdas); on_commit runs after commit so the checkpoint never
            # leads committed data (DATA_INGESTION §3.5).
            transaction.on_commit(
                lambda: advance_checkpoint(
                    provider,
                    checkpoint_scope,
                    last_delivery_seq=last_delivery_seq,
                    last_provider_updated_at=last_provider_updated_at,
                    cursor=checkpoint_cursor,
                )
            )
        core_events.publish(
            core_events.TOPIC_INGESTION_COMPLETED,
            _event_payload(job, spec, status, ctx),
        )
        return _report(job, spec, action, status, ctx)


def _report(
    job: ImportJob,
    spec: PipelineSpec,
    action: str,
    status: str,
    ctx: IngestContext | None = None,
    *,
    error: str | None = None,
    error_class: str | None = None,
) -> PipelineReport:
    return PipelineReport(
        job_id=str(job.id),
        idempotency_key=spec.idempotency_key,
        action=action,
        status=status,
        job_type=spec.job_type,
        provider_code=spec.provider_code,
        read=ctx.read if ctx else 0,
        written=ctx.written if ctx else 0,
        skipped=ctx.skipped if ctx else 0,
        errors=ctx.errors if ctx else 0,
        error=error,
        error_class=error_class,
    )


def _event_payload(
    job: ImportJob,
    spec: PipelineSpec,
    status: str,
    ctx: IngestContext,
    *,
    error: str | None = None,
    error_class: str | None = None,
) -> dict[str, Any]:
    return {
        "job_id": str(job.id),
        "idempotency_key": spec.idempotency_key,
        "provider_code": spec.provider_code,
        "job_type": spec.job_type,
        "status": status,
        "read": ctx.read,
        "written": ctx.written,
        "skipped": ctx.skipped,
        "errors": ctx.errors,
        "error": error,
        "error_class": error_class,
    }


__all__ = [
    "IngestContext",
    "PipelineError",
    "PipelineFactory",
    "PipelineNotRegistered",
    "PipelineReport",
    "PipelineSpec",
    "StageResult",
    "get_pipeline",
    "list_pipelines",
    "register_pipeline",
    "run_pipeline",
    "run_pipeline_spec",
]
