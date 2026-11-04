"""Synchronous event handlers that write the audit trail.

Subscribed from :class:`apps.administration.apps.AdministrationConfig.ready`.
They run ``on_commit=False`` (same transaction as the change they record) so an
``AuditLog`` row commits or rolls back **with** the change — the audit trail can
never describe a change that did not happen, and a failed write never leaves a
half-recorded audit row.
"""

from __future__ import annotations

from typing import Any

from apps.administration.models import AuditLog
from apps.core.events import (
    TOPIC_CORRECTION_REQUESTED,
    TOPIC_DUPLICATE_REVIEWED,
    TOPIC_MATCH_REOPENED,
    TOPIC_PLAYER_MERGED,
    TOPIC_PROVIDER_MAP_VERIFIED,
)


def on_player_merged(payload: Any) -> None:
    """Record a completed player merge (payload from ``merge_players``)."""
    AuditLog.objects.create(
        action=TOPIC_PLAYER_MERGED,
        entity_type="player",
        entity_id=payload["canonical_player_id"],
        after={
            "canonical_player_id": payload["canonical_player_id"],
            "merged_player_id": payload["merged_player_id"],
        },
        actor_label=str(payload.get("actor")),
    )


def on_provider_map_verified(payload: Any) -> None:
    """Record a steward verifying a provider-entity mapping (``verify_mapping``)."""
    AuditLog.objects.create(
        action=TOPIC_PROVIDER_MAP_VERIFIED,
        entity_type="provider_entity_map",
        entity_id=payload["provider_entity_map_id"],
        actor_label=str(payload.get("actor")),
    )


def on_duplicate_reviewed(payload: Any) -> None:
    """Record a duplicate-candidate review decision (reject/merge, Group 5)."""
    AuditLog.objects.create(
        action=TOPIC_DUPLICATE_REVIEWED,
        entity_type="player_duplicate_candidate",
        entity_id=payload["duplicate_candidate_id"],
        after={
            "canonical_player_id": payload.get("canonical_player_id"),
            "decision": payload.get("decision"),
        },
        actor_label=str(payload.get("actor")),
    )


def on_match_reopened(payload: Any) -> None:
    """Audit a terminal match being reopened for a provider correction."""
    AuditLog.objects.create(
        action=TOPIC_MATCH_REOPENED,
        entity_type="match",
        entity_id=payload["match_id"],
        after={
            "reason": payload.get("reason"),
            "source": payload.get("source"),
        },
        actor_label=str(payload.get("actor")),
    )


def on_correction_requested(payload: Any) -> None:
    """Audit a steward-requested manual correction (before it is applied)."""
    AuditLog.objects.create(
        action=TOPIC_CORRECTION_REQUESTED,
        entity_type=payload.get("entity_type"),
        entity_id=payload.get("entity_id"),
        before=payload.get("old_value"),
        after=payload.get("new_value"),
        actor_label=str(payload.get("actor")),
    )


__all__ = [
    "on_correction_requested",
    "on_duplicate_reviewed",
    "on_match_reopened",
    "on_player_merged",
    "on_provider_map_verified",
]
