"""Narrow realtime protocols (ARCHITECTURE §2, Phase E ledger conflict 1).

``apps/realtime`` depends only on ``apps.core`` (import-linter enforced). The
durable per-match event log lives in ``apps/matches`` (``DATABASE_SCHEMA.md``
wins on tables). To reconcile the two, this module — inside the leaf ``core`` —
defines the *narrow interfaces* realtime needs: an event-log reader (replay,
backfill, resync signalling) and a per-match delivery policy (licensing
degradation). ``apps/matches/services/stream_reader.py`` implements them and
the composition root (``config/asgi.py`` + ``apps.realtime.wiring``) injects
the implementation.

This module is pure data + protocols: no Django, no first-party imports, so
the pure-scoring suites that import ``core`` keep working.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

#: Event types the hub flushes immediately instead of coalescing (Phase E
#: ledger decision 6): a correction changes history and a completion is a
#: terminal transition — neither may wait for the ≤1 Hz tick.
IMMEDIATE_EVENT_TYPES: frozenset[str] = frozenset({"correction.applied", "match.completed"})

#: Client-visible control frames produced by the hub itself (not from the log).
CONTROL_EVENT_TYPES: frozenset[str] = frozenset({"batch", "resync_required", "server.draining"})


@dataclass(frozen=True)
class StreamEvent:
    """One event as the log reader hands it to realtime (API_SPEC §3.8)."""

    seq: int
    event_type: str
    match_id: str
    #: The event-schema envelope (``{"v": …, "match_id": …, …}``).
    payload: dict[str, Any] = field(default_factory=dict)
    #: ISO-8601 UTC timestamp of the log row.
    created_at: str = ""


@dataclass(frozen=True)
class StreamInfo:
    """Result of a replay query (recovery contract, API_SPEC §3.8).

    ``events`` is a gapless, ascending run after ``last_seq``. When the
    requested position is in the future or predates the retained window the
    reader returns ``resync_required=True`` with no events: neither case can be
    replayed without gaps, so the client must refetch the REST snapshot.
    """

    events: tuple[StreamEvent, ...] = ()
    resync_required: bool = False
    reason: str | None = None

    def __bool__(self) -> bool:
        return bool(self.events)


class EventLogReader(Protocol):
    """Anything that can replay the durable per-match event log.

    Implementations are synchronous (Django ORM); the async hub calls them
    directly — replay/backfill is rare and the reads are single-row-indexed.
    """

    def events_since(self, match_id: str, last_seq: int) -> StreamInfo:
        """Events after ``last_seq``, or a ``resync_required`` signal."""

    def latest_seq(self, match_id: str) -> int:
        """The current ``MAX(seq)`` for ``match_id`` (0 when the log is empty)."""


class MatchPolicy(Protocol):
    """Per-match delivery policy (licensing degradation, SECURITY §11).

    ``allowed_event_types`` returns the set of event types licensed for the
    match, or ``None`` when every type is allowed. The hub tracks the sequence
    of filtered events (so ordering stays consistent) but does not deliver
    them. A policy change requires a fresh subscription or hub restart — the
    decision is resolved per match fanout, never per frame.
    """

    def allowed_event_types(self, match_id: str) -> frozenset[str] | None: ...


__all__ = [
    "CONTROL_EVENT_TYPES",
    "EVENT_LOG_READER_DEFAULT_LATEST",
    "IMMEDIATE_EVENT_TYPES",
    "EventLogReader",
    "MatchPolicy",
    "StreamEvent",
    "StreamInfo",
]

#: Sentinel documented for implementations: ``latest_seq`` on an empty log.
EVENT_LOG_READER_DEFAULT_LATEST = 0
