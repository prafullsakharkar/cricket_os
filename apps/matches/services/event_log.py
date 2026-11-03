"""Durable, per-match ordered event log (DOMAIN_MODEL §3.12, API_SPEC §3.8).

Design (justification recorded in the roadmap's Phase D decisions):

* **Advisory transaction lock, not a counter row.** ``pg_advisory_xact_lock``
  keyed by the match UUID is held until the surrounding transaction commits or
  rolls back. Concurrent appends serialise, ``MAX(seq)+1`` is therefore safe,
  and a rollback removes the event and leaves **no gap** without maintaining a
  second counter row that could itself drift.
* **The log is the source of truth for realtime.** Publication to Redis runs
  in ``transaction.on_commit`` and is best-effort; the row is already durable.
* **Retention** removes rows only for terminal matches after the configured
  post-completion window; an in-progress match is never pruned.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from django.db import connection, transaction
from django.db.models import Max, Min, Q

from apps.core import metrics
from apps.matches import event_schemas
from apps.matches.models import Match, MatchEventLog
from apps.matches.services import publication

logger = logging.getLogger("apps.matches.event_log")

#: Terminal lifecycle states (mirrors ``matches.services.state.TERMINAL``).
_TERMINAL = frozenset({"COMPLETED", "ABANDONED", "CANCELLED", "FORFEITED"})


def advisory_key(match_id: Any) -> int:
    """A stable signed-64-bit advisory-lock key for a match UUID."""
    value = match_id if isinstance(match_id, UUID) else UUID(str(match_id))
    return int.from_bytes(value.bytes[:8], "big", signed=True)


def _lock(match_id: Any) -> None:
    """Take the transaction-scoped per-match advisory lock."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [advisory_key(match_id)])


def _next_seq(match_id: Any) -> int:
    current = (
        MatchEventLog.objects.filter(match_id=match_id)
        .order_by("-seq")
        .values_list("seq", flat=True)
        .first()
    )
    return (current or 0) + 1


def append_event(
    match_id: Any,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    publish: bool = True,
) -> MatchEventLog:
    """Append one event with the next per-match ``seq``.

    Must be called inside the same transaction as the state change it
    describes. The transaction-scoped advisory lock guarantees gapless,
    monotonic sequencing across concurrent writers.
    """
    rows = append_events(match_id, [(event_type, payload or {})], publish=publish)
    return rows[0]


def append_events(
    match_id: Any,
    events: list[tuple[str, dict[str, Any]]],
    *,
    publish: bool = True,
) -> list[MatchEventLog]:
    """Append a batch of events atomically under one advisory lock."""
    if not events:
        return []
    for event_type, _payload in events:
        if event_type not in event_schemas.EVENT_TYPES:
            raise ValueError(f"unknown event type {event_type!r}")

    def _write() -> list[MatchEventLog]:
        _lock(match_id)
        started = datetime.datetime.now(datetime.UTC)
        rows: list[MatchEventLog] = []
        seq = _next_seq(match_id)
        for event_type, payload in events:
            body = event_schemas.envelope(event_type, str(match_id), payload)
            rows.append(
                MatchEventLog.objects.create(
                    match_id=match_id,
                    seq=seq,
                    event_type=event_type,
                    payload=body,
                )
            )
            seq += 1
        metrics.EVENT_LOG_LAG.set(0)
        metrics.INGEST_LATENCY.observe(
            (datetime.datetime.now(datetime.UTC) - started).total_seconds()
        )
        return rows

    # A caller already inside the ingestion transaction must stay there so the
    # log row and the state change commit/roll back together.
    if transaction.get_connection().in_atomic_block:
        rows = _write()
    else:  # pragma: no cover - defensive for ad-hoc callers
        with transaction.atomic():
            rows = _write()

    if publish:
        for row in rows:
            event = {
                "seq": row.seq,
                "event_type": row.event_type,
                "match_id": str(row.match_id),
                "created_at": row.created_at.isoformat(),
                **row.payload,
            }
            transaction.on_commit(
                lambda event=event, match_id=match_id: publication.publish_match_event(
                    match_id, event
                )
            )
    return rows


@dataclass(frozen=True)
class EventsSince:
    """Result of :func:`events_since` (API_SPEC §3.8 recovery contract)."""

    events: list[dict[str, Any]] = field(default_factory=list)
    resync_required: bool = False
    reason: str | None = None

    def __bool__(self) -> bool:
        return bool(self.events)


def events_since(match_id: Any, last_seq: int) -> EventsSince:
    """Events after ``last_seq`` or a ``resync_required`` signal.

    ``resync`` is returned when the requested position is in the future, or
    when it predates the retained window (the log was pruned after
    completion), because neither case can be replayed without gaps.
    """
    if last_seq < 0:
        return EventsSince(resync_required=True, reason="invalid_last_seq")
    bounds = MatchEventLog.objects.filter(match_id=match_id).aggregate(
        first=Min("seq"), last=Max("seq")
    )
    first, last = bounds["first"], bounds["last"]
    if last is None:
        # No retained events: only a client already at the start is in sync.
        if last_seq == 0:
            return EventsSince()
        return EventsSince(resync_required=True, reason="future_seq")
    if last_seq > last:
        return EventsSince(resync_required=True, reason="future_seq")
    if first is not None and last_seq < first - 1:
        # The requested position predates the retained range: replaying only
        # the surviving rows would silently skip pruned events.
        return EventsSince(resync_required=True, reason="retention_window")
    rows = list(
        MatchEventLog.objects.filter(match_id=match_id, seq__gt=last_seq)
        .order_by("seq")
        .values("seq", "event_type", "payload", "created_at")
    )
    return EventsSince(events=[_event_dict(row) for row in rows])


def _event_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "seq": row["seq"],
        "event_type": row["event_type"],
        "match_id": str(row["payload"].get("match_id", "")),
        "payload": row["payload"],
        "created_at": row["created_at"].isoformat(),
    }


@dataclass(frozen=True)
class PruneReport:
    """What the retention job removed (or would remove)."""

    matches: list[str]
    events: int
    dry_run: bool


def prune_events(
    *, retention: datetime.timedelta = datetime.timedelta(hours=24), dry_run: bool = True
) -> PruneReport:
    """Delete event-log rows for matches completed before ``retention``.

    Never touches a match that is not terminal (an in-progress match keeps its
    full log regardless of age). ``dry_run`` is the default so an operator must
    opt in explicitly.
    """
    cutoff = datetime.datetime.now(datetime.UTC) - retention
    candidates = Match.objects.filter(status__in=_TERMINAL).filter(
        Q(end_time_utc__lt=cutoff) | Q(end_time_utc__isnull=True, last_ingested_at__lt=cutoff)
    )
    match_ids = [str(pk) for pk in candidates.values_list("id", flat=True)]
    if not match_ids:
        return PruneReport(matches=[], events=0, dry_run=dry_run)
    query = MatchEventLog.objects.filter(match_id__in=match_ids, created_at__lt=cutoff)
    count = query.count()
    if not dry_run:
        query.delete()
        logger.info("pruned match events", extra={"matches": match_ids, "events": count})
    return PruneReport(matches=match_ids, events=count, dry_run=dry_run)


__all__ = [
    "EventsSince",
    "PruneReport",
    "advisory_key",
    "append_event",
    "append_events",
    "events_since",
    "prune_events",
]
