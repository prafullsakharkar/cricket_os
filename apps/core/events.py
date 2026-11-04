"""In-process event bus (ARCHITECTURE §5).

Writes use ``transaction.atomic()``; events are published *after commit* via
``transaction.on_commit`` so consumers never observe rolled-back data. The
``on_commit=False`` escape hatch is reserved for synchronous, same-transaction
subscribers — the audit trail must commit or roll back with its change.

Handlers are registered in each app's ``AppConfig.ready()`` with
:func:`subscribe`. A handler that raises propagates: for post-commit handlers
that is an application error after the commit; for synchronous handlers it
fails the surrounding transaction (which is exactly what audit wants).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.db import transaction

#: Audit-worthy topic: a duplicate player merge completed.
TOPIC_PLAYER_MERGED = "player.merged"
#: Audit-worthy topic: a provider-entity mapping was verified by a steward.
TOPIC_PROVIDER_MAP_VERIFIED = "provider_map.verified"
#: Audit-worthy topic: a duplicate candidate was reviewed (rejected/merged).
TOPIC_DUPLICATE_REVIEWED = "player_duplicate.reviewed"
#: Tier-1: new deliveries were appended to an innings (live path).
TOPIC_DELIVERY_APPENDED = "delivery.appended"
#: Tier-1: previously-imported deliveries were superseded by a correction.
TOPIC_DELIVERY_CORRECTED = "delivery.corrected"
#: Tier-1: derived scorecard rows were rebuilt for an innings/match.
TOPIC_SCORECARD_UPDATED = "scorecard.updated"
#: Tier-2: a match completed; statistics may recompute.
TOPIC_MATCH_COMPLETED = "match.completed"
#: Tier-2: a data correction was applied to a completed match.
TOPIC_CORRECTION_APPLIED = "correction.applied"
#: Ingestion: an ImportJob completed (succeeded or partial); checkpoint advanced.
TOPIC_INGESTION_COMPLETED = "ingestion.completed"
#: Ingestion: an ImportJob failed permanently (a DLQ row was written).
TOPIC_INGESTION_FAILED = "ingestion.failed"
#: Audit-worthy topic: a terminal match was reopened for a provider correction.
TOPIC_MATCH_REOPENED = "match.reopened"
#: Audit-worthy topic: a steward requested a manual correction (queued/approved).
TOPIC_CORRECTION_REQUESTED = "correction.requested"
#: Cache invalidation: a match lifecycle/state row changed (Phase E).
TOPIC_MATCH_STATE_CHANGED = "match.state_changed"
#: Cache invalidation: a match shell/result/metadata row changed (Phase E).
TOPIC_MATCH_UPDATED = "match.updated"
#: Cache invalidation: an innings summary/progression changed (Phase E).
TOPIC_INNINGS_UPDATED = "innings.updated"
#: Cache invalidation: a player profile changed (Phase E reference data).
TOPIC_PLAYER_UPDATED = "player.updated"
#: Cache invalidation: a team profile changed (Phase E reference data).
TOPIC_TEAM_UPDATED = "team.updated"
#: Cache invalidation: a venue profile changed (Phase E reference data).
TOPIC_VENUE_UPDATED = "venue.updated"
#: Cache invalidation: a series changed (Phase E reference data).
TOPIC_SERIES_UPDATED = "series.updated"
#: Cache invalidation: a competition changed (Phase E reference data).
TOPIC_COMPETITION_UPDATED = "competition.updated"

_handlers: dict[str, list[Callable[[Any], None]]] = {}


def subscribe(topic: str, handler: Callable[[Any], None]) -> None:
    """Register ``handler(payload)`` to run for ``topic`` on every publish."""
    _handlers.setdefault(topic, []).append(handler)


def _dispatch(topic: str, payload: Any) -> None:
    """Call every handler for ``topic``; exceptions propagate to the caller."""
    for handler in list(_handlers.get(topic, ())):
        handler(payload)


def publish(topic: str, payload: Any, *, on_commit: bool = True) -> None:
    """Publish ``payload`` on ``topic``.

    By default handlers run after the surrounding transaction commits
    (``transaction.on_commit``). Pass ``on_commit=False`` for synchronous
    in-transaction subscribers such as the audit log.
    """
    if on_commit:
        transaction.on_commit(lambda: _dispatch(topic, payload))
    else:
        _dispatch(topic, payload)


def clear_subscriptions() -> None:
    """Drop every registered handler (test isolation)."""
    _handlers.clear()


__all__ = [
    "TOPIC_COMPETITION_UPDATED",
    "TOPIC_CORRECTION_APPLIED",
    "TOPIC_CORRECTION_REQUESTED",
    "TOPIC_DELIVERY_APPENDED",
    "TOPIC_DELIVERY_CORRECTED",
    "TOPIC_DUPLICATE_REVIEWED",
    "TOPIC_INGESTION_COMPLETED",
    "TOPIC_INGESTION_FAILED",
    "TOPIC_INNINGS_UPDATED",
    "TOPIC_MATCH_COMPLETED",
    "TOPIC_MATCH_REOPENED",
    "TOPIC_MATCH_STATE_CHANGED",
    "TOPIC_MATCH_UPDATED",
    "TOPIC_PLAYER_MERGED",
    "TOPIC_PLAYER_UPDATED",
    "TOPIC_PROVIDER_MAP_VERIFIED",
    "TOPIC_SCORECARD_UPDATED",
    "TOPIC_SERIES_UPDATED",
    "TOPIC_TEAM_UPDATED",
    "TOPIC_VENUE_UPDATED",
    "clear_subscriptions",
    "publish",
    "subscribe",
]
