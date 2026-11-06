"""PostgresEventLogReader + LicensingMatchPolicy (recovery contract, §3.8).

The reader is the matches-side implementation of ``core.streaming`` protocols;
the hub/broker use it for replay, gap backfill and degraded polling. These are
real-DB integration tests: append events through the canonical service, then
read them back, including the resync signalling for unreplayable positions.
"""

from __future__ import annotations

import datetime

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.core.streaming import StreamInfo
from apps.matches import event_schemas
from apps.matches.models import MatchStatus
from apps.matches.services import event_log
from apps.matches.services.stream_reader import (
    LicensingMatchPolicy,
    PostgresEventLogReader,
)
from tests.phase_c.factories import make_match, make_provider

pytestmark = pytest.mark.django_db(transaction=True)


def _append(match_id: object, seq: int) -> None:
    event_log.append_event(
        match_id,
        event_schemas.DELIVERY_APPENDED,
        {"delivery_seq": seq},
    )


def test_events_since_replays_ascending_committed_run() -> None:
    match = make_match()
    for seq in (1, 2, 3):
        _append(match.id, seq)
    reader = PostgresEventLogReader()

    info = reader.events_since(str(match.id), last_seq=1)
    assert isinstance(info, StreamInfo)
    assert [e.seq for e in info.events] == [2, 3]
    assert info.resync_required is False
    assert all(e.match_id == str(match.id) for e in info.events)
    assert all(e.event_type == event_schemas.DELIVERY_APPENDED for e in info.events)
    assert reader.latest_seq(str(match.id)) == 3


def test_latest_seq_is_zero_for_empty_log() -> None:
    reader = PostgresEventLogReader()
    assert reader.latest_seq("01a1a5ac-0000-7000-0000-000000000000") == 0


def test_future_seq_requires_resync() -> None:
    match = make_match()
    _append(match.id, 1)
    info = PostgresEventLogReader().events_since(str(match.id), last_seq=99)
    assert info.resync_required is True
    assert info.reason == "future_seq"
    assert info.events == ()


def test_empty_log_with_nonzero_last_seq_requires_resync() -> None:
    match = make_match()
    reader = PostgresEventLogReader()
    info = reader.events_since(str(match.id), last_seq=3)
    assert info.resync_required is True
    assert info.reason == "future_seq"
    # A fresh client at the start is in sync.
    fresh = reader.events_since(str(match.id), last_seq=0)
    assert fresh.resync_required is False


def test_retention_window_resync_after_prune() -> None:
    from apps.matches.models import MatchEventLog

    done = make_match(
        status=MatchStatus.COMPLETED,
        end_time_utc=timezone.now() - datetime.timedelta(days=2),
    )
    for seq in (1, 2, 3, 4, 5, 6):
        _append(done.id, seq)
    # Age only the first three rows so retention prunes them, leaving 4-6.
    MatchEventLog.objects.filter(match_id=done.id, seq__lte=3).update(
        created_at=timezone.now() - datetime.timedelta(days=2)
    )
    event_log.prune_events(dry_run=False)

    reader = PostgresEventLogReader()
    # A client at seq 2 predates the retained range (4-6): resync.
    info = reader.events_since(str(done.id), last_seq=2)
    assert info.resync_required is True
    assert info.reason == "retention_window"
    # A client already past the gap replays what survived.
    fresh = reader.events_since(str(done.id), last_seq=3)
    assert fresh.resync_required is False
    assert [e.seq for e in fresh.events] == [4, 5, 6]
    assert reader.latest_seq(str(done.id)) == 6


def test_events_since_includes_payload_and_timestamps() -> None:
    match = make_match()
    event_log.append_event(
        match.id,
        event_schemas.CORRECTION_APPLIED,
        {"digest": "abc", "revision": 2},
    )
    info = PostgresEventLogReader().events_since(str(match.id), 0)
    assert len(info.events) == 1
    event = info.events[0]
    assert event.payload["digest"] == "abc"
    assert event.payload["v"] == event_schemas.SCHEMA_VERSION
    assert event.created_at  # ISO timestamp


# --------------------------------------------------------------------------- #
# Licensing policy (SECURITY §11)
# --------------------------------------------------------------------------- #


def test_policy_allows_everything_when_ball_data_is_licensed() -> None:
    provider = make_provider()
    match = make_match(source_provider_id=provider.id)
    assert LicensingMatchPolicy().allowed_event_types(str(match.id)) is None


def test_policy_filters_ball_events_when_not_licensed() -> None:
    provider = make_provider(code="strict")
    match = make_match(source_provider_id=provider.id)
    with override_settings(
        LICENSED_CONTENT_POLICIES={
            "strict": {
                "display_rights": False,
                "commentary_allowed": False,
                "derived_feeds_allowed": False,
            }
        }
    ):
        allowed = LicensingMatchPolicy().allowed_event_types(str(match.id))
    assert allowed is not None
    assert "delivery.appended" not in allowed
    assert "delivery.corrected" not in allowed
    assert "match.completed" in allowed
    assert "correction.applied" in allowed
    assert "scorecard.updated" in allowed


def test_policy_defaults_for_match_without_provider() -> None:
    match = make_match()
    assert LicensingMatchPolicy().allowed_event_types(str(match.id)) is None
    assert (
        LicensingMatchPolicy().allowed_event_types("00000000-0000-0000-0000-000000000000") is None
    )
