"""Checkpoint service for the ingestion pipeline (DATA_INGESTION §3.5/§4.7).

``SyncCheckpoint`` records the last committed position per ``(provider,
scope_key)`` so a crashed import can resume without duplicating or losing
matches. The pipeline calls :func:`advance_checkpoint` **after the surrounding
transaction commits** (via ``transaction.on_commit``) — a checkpoint is never
ahead of committed data, so restart safety follows (DATA_INGESTION §4.7).

Owned by ``synchronization``; ``ingestion`` depends on this module, never the
reverse (ARCHITECTURE §3).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.utils import timezone

from apps.synchronization.models import SyncCheckpoint


def get_checkpoint(provider: Any, scope_key: str) -> SyncCheckpoint | None:
    """The last committed checkpoint for ``(provider, scope_key)`` or ``None``."""
    return SyncCheckpoint.objects.filter(provider=provider, scope_key=scope_key).first()


def resume_cursor(provider: Any, scope_key: str) -> str | None:
    """The opaque resume cursor for a scope, or ``None`` when never synced."""
    checkpoint = get_checkpoint(provider, scope_key)
    return checkpoint.cursor if checkpoint is not None else None


def advance_checkpoint(
    provider: Any,
    scope_key: str,
    *,
    last_delivery_seq: int | None = None,
    last_provider_updated_at: datetime | None = None,
    cursor: str | None = None,
) -> SyncCheckpoint:
    """Upsert the checkpoint for ``(provider, scope_key)`` to the new position.

    Call only after the data it describes has committed. Missing fields keep
    their previous value (partial updates are never overwritten with nulls —
    DATA_INGESTION §4.4).
    """
    row, _ = SyncCheckpoint.objects.update_or_create(
        provider=provider,
        scope_key=scope_key,
        defaults={
            "last_delivery_seq": last_delivery_seq,
            "last_provider_updated_at": last_provider_updated_at,
            "cursor": cursor,
            "last_run_at": timezone.now(),
        },
    )
    return row


__all__ = ["advance_checkpoint", "get_checkpoint", "resume_cursor"]
