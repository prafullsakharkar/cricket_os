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


def _report_unresolved_ranking(row: Any, *, provider: Any = None) -> None:
    """DLQ writer for the rankings import pipeline (Group 5).

    The pipeline resolves provider player ids through ``provider_entity_map``;
    a row whose provider id has no mapping is surfaced here so ops can seed the
    identity map and re-import. Payload is redacted by ``record_error``.
    """
    from apps.ingestion.services.jobs import record_error

    record_error(
        None,
        stage="rankings_import",
        payload={
            "provider_player_id": row.get("provider_player_id"),
            "player_name": row.get("player_name"),
        },
        error_class="UnresolvedRankingPlayer",
        error_detail=(
            f"provider player id {row.get('provider_player_id')!r} has no "
            "provider_entity_map row; seed the identity map and re-import"
        ),
        provider_id=provider.pk if provider is not None else None,
    )


def register_default_reporters() -> None:
    """Wire the reconciliation + shadow-verify + rankings DLQ reporters."""
    from apps.ingestion.services.jobs import reconciliation_mismatch_reporter
    from apps.live.services.verify import register_shadow_verify_reporter
    from apps.rankings.services.import_rankings import register_unresolved_reporter
    from apps.synchronization.services.reconciliation import register_mismatch_reporter

    register_mismatch_reporter(reconciliation_mismatch_reporter)
    register_shadow_verify_reporter(_report_shadow_verify_mismatch)
    register_unresolved_reporter(_report_unresolved_ranking)


__all__ = ["register_default_reporters"]
