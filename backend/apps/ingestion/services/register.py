"""Ingestion-pipeline wrap of the Cricsheet register importer.

``players.import_register`` is a pure, sink-driven importer (Phase B). This
module runs it inside the Phase C pipeline so a register import gets the same
``ImportJob`` idempotency, DLQ and event guarantees as match data: the run is
keyed by ``idempotency_key`` (re-runs are no-ops), every rejected row becomes a
redacted ``IngestionError`` row, and the job is marked ``partial`` when any row
was rejected. The register importer never raises on bad rows — it counts them —
so the only permanent failure here is an unexpected sink/database error, which
the pipeline turns into a ``PipelineError``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from django.db import transaction

from apps.ingestion.services.jobs import record_error
from apps.ingestion.services.pipeline import (
    IngestContext,
    PipelineError,
    PipelineReport,
    PipelineSpec,
    StageResult,
    run_pipeline_spec,
)
from apps.players.import_register import Kind, import_register
from apps.players.sinks import DjangoRegisterSink

#: Idempotency-key prefix for register imports (provider-agnostic; the caller
#: composes the full key, e.g. ``register:people:<sha256>``).
KEY_PREFIX = "register"


def run_register_import(
    rows: Sequence[Mapping[str, str]],
    *,
    kind: Kind,
    idempotency_key: str,
    provider_code: str = "cricsheet",
    scope: dict[str, Any] | None = None,
    created_by: Any = None,
) -> PipelineReport:
    """Import a register through the pipeline (ImportJob + DLQ + events)."""
    spec = PipelineSpec(
        provider_code=provider_code,
        job_type=f"register:{kind}",
        idempotency_key=idempotency_key,
        scope=scope or {"kind": kind, "rows": len(rows)},
        stages=[_stage(rows, kind=kind, provider_code=provider_code)],
    )
    return run_pipeline_spec(spec, created_by=created_by)


def _stage(
    rows: Sequence[Mapping[str, str]],
    *,
    kind: Kind,
    provider_code: str,
) -> Any:
    def stage(ctx: IngestContext) -> StageResult:
        try:
            with transaction.atomic():
                sink = DjangoRegisterSink(provider_code=provider_code)
                report = import_register(rows, sink, kind=kind)
        except Exception as exc:
            raise PipelineError(
                str(exc),
                stage="persist",
                error_class=type(exc).__name__,
                payload={"kind": kind, "rows": len(rows)},
            ) from exc
        # Rejected rows go to the DLQ (redacted) and make the job partial.
        for entry in report.errors:
            record_error(
                ctx.job,
                stage="validate",
                payload={"kind": kind, **entry},
                error_class="RegisterRowError",
                error_detail=str(entry.get("reason")),
            )
        return StageResult(
            read=report.read,
            written=report.created + report.updated,
            skipped=report.skipped,
            errors=len(report.errors),
        )

    return stage


__all__ = ["KEY_PREFIX", "run_register_import"]
