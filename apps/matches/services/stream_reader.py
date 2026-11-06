"""PostgreSQL-backed implementations of the realtime protocols.

Implements :class:`apps.core.streaming.EventLogReader` over the durable
``match_event_log`` (via ``apps.matches.services.event_log``) and a per-match
:class:`apps.core.streaming.MatchPolicy` derived from the provider licensing
configuration. The composition root injects these into ``apps/realtime``, so
the realtime app itself never imports this module.

The licensing policy lives here (not in ``apps/providers``) because it needs
the ``Match`` model to resolve the match's source provider; the
``matches → providers`` dependency already exists in the codebase.
"""

from __future__ import annotations

import logging

from apps.core.streaming import StreamEvent, StreamInfo
from apps.matches.services import event_log

logger = logging.getLogger("apps.matches.stream_reader")

#: Event types that carry ball-level data (gated by the ``ball_data`` licence).
_BALL_LEVEL_EVENTS = frozenset({"delivery.appended", "delivery.corrected"})


class PostgresEventLogReader:
    """Replay the per-match event log from PostgreSQL."""

    def events_since(self, match_id: str, last_seq: int) -> StreamInfo:
        result = event_log.events_since(match_id, last_seq)
        events = tuple(
            StreamEvent(
                seq=row["seq"],
                event_type=row["event_type"],
                match_id=str(row["match_id"]),
                payload=dict(row["payload"]),
                created_at=row["created_at"],
            )
            for row in result.events
        )
        return StreamInfo(
            events=events,
            resync_required=result.resync_required,
            reason=result.reason,
        )

    def latest_seq(self, match_id: str) -> int:
        from apps.matches.models import MatchEventLog

        current = (
            MatchEventLog.objects.filter(match_id=match_id)
            .order_by("-seq")
            .values_list("seq", flat=True)
            .first()
        )
        return current or 0


class LicensingMatchPolicy:
    """Resolve the licensed event types for a match from its source provider.

    A match whose provider lacks ``ball_data`` rights has ball-level events
    filtered from delivery (the hub still advances their sequence, so a
    subscriber never sees a gap). A match without a source provider, or with a
    provider that allows ball data, delivers everything.
    """

    def allowed_event_types(self, match_id: str) -> frozenset[str] | None:
        from apps.matches.models import Match
        from apps.providers.licensing import FEATURE_BALL_DATA, policy_for
        from apps.providers.models import DataProvider

        match = Match.objects.filter(pk=match_id).only("source_provider_id").first()
        if match is None or match.source_provider_id is None:
            return None
        # ``source_provider_id`` is a plain UUID column (decision D2); resolve
        # the provider so policy_for reads its code.
        provider = DataProvider.objects.filter(pk=match.source_provider_id).only("code").first()
        if provider is None:
            return None
        policy = policy_for(provider)
        if policy.allows(FEATURE_BALL_DATA):
            return None
        return frozenset(
            {
                "innings.updated",
                "match.state_changed",
                "match.updated",
                "match.completed",
                "correction.applied",
                "scorecard.updated",
            }
        )


__all__ = ["LicensingMatchPolicy", "PostgresEventLogReader", "logger"]
