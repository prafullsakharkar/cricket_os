"""Dependency-inverted reporters registered by ``apps.ingestion``.

Synchronization (reconciliation) and the live surface (shadow verify) must not
import ``apps.ingestion`` (the import-linter contract forbids
``synchronization -> ingestion`` and, transitively via ``apps.live``,
``providers -> ingestion``). Instead they expose reporter hooks, and this
module — the ingestion side — registers the ``IngestionError`` DLQ writers in
``IngestionConfig.ready``.

Tests call :func:`register_default_reporters` per test so the DLQ rows the
Phase D suite asserts on are always observable regardless of ``ready()``
ordering.
"""

from __future__ import annotations

from typing import Any


def _report_shadow_verify_mismatch(innings: Any) -> None:
    from apps.ingestion.services.jobs import record_error

    record_error(
        None,
        stage="shadow_verify",
        payload={"match_id": str(innings.match_id), "innings_id": str(innings.id)},
        error_class="ShadowVerifyMismatch",
        error_detail="persisted Tier-1 rows differed from a fresh engine rebuild; healed",
    )


def register_default_reporters() -> None:
    """Wire the reconciliation + shadow-verify DLQ reporters (idempotent)."""
    from apps.ingestion.services.jobs import reconciliation_mismatch_reporter
    from apps.live.services.verify import register_shadow_verify_reporter
    from apps.synchronization.services.reconciliation import register_mismatch_reporter

    register_mismatch_reporter(reconciliation_mismatch_reporter)
    register_shadow_verify_reporter(_report_shadow_verify_mismatch)


__all__ = ["register_default_reporters"]
