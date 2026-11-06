"""Phase D group 2: durable per-match event log, sequencing, publication."""

from __future__ import annotations

import datetime
from concurrent.futures import ThreadPoolExecutor

import jsonschema
import pytest
from django.db import connection, transaction
from django.utils import timezone

from apps.core import metrics
from apps.core.enums import MatchStatus
from apps.matches import event_schemas
from apps.matches.models import MatchEventLog
from apps.matches.services import event_log, state
from apps.matches.services import publication as pub

pytestmark = pytest.mark.django_db


def _append(match_id, index: int) -> int:
    row = event_log.append_event(
        match_id,
        event_schemas.DELIVERY_APPENDED,
        {"innings_id": "00000000-0000-0000-0000-000000000001", "delivery_seqs": [index]},
    )
    return row.seq


def test_append_allocates_gapless_per_match_sequences(make_match) -> None:
    match = make_match()
    other = make_match()
    seqs = [
        event_log.append_event(
            match.id,
            event_schemas.DELIVERY_APPENDED,
            {"innings_id": str(match.id), "delivery_seqs": [n]},
        ).seq
        for n in range(1, 6)
    ]
    assert seqs == [1, 2, 3, 4, 5]
    # A different match has its own sequence.
    assert _append(other.id, 1) == 1


def test_rollback_leaves_no_event_and_no_gap(make_match) -> None:
    match = make_match()
    with pytest.raises(RuntimeError), transaction.atomic():
        event_log.append_event(
            match.id,
            event_schemas.SCORECARD_UPDATED,
            {"digest": "x", "revision": 2},
        )
        raise RuntimeError("boom")
    assert MatchEventLog.objects.filter(match_id=match.id).count() == 0
    assert _append(match.id, 1) == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_appends_are_gapless_and_unique(make_match) -> None:
    match = make_match()

    def worker(n: int) -> int:
        try:
            return _append(match.id, n)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        seqs = list(pool.map(worker, range(1, 21)))

    assert sorted(seqs) == list(range(1, 21))
    stored = list(
        MatchEventLog.objects.filter(match_id=match.id)
        .order_by("seq")
        .values_list("seq", flat=True)
    )
    assert stored == list(range(1, 21))


def test_unknown_event_type_is_rejected(make_match) -> None:
    match = make_match()
    with pytest.raises(ValueError, match="unknown event type"):
        event_log.append_event(match.id, "bogus.event", {})


def test_every_event_payload_matches_its_json_schema() -> None:
    samples = {
        event_schemas.DELIVERY_APPENDED: {
            "innings_id": "i",
            "delivery_seqs": [1, 2],
        },
        event_schemas.DELIVERY_CORRECTED: {
            "innings_id": "i",
            "delivery_seqs": [2],
            "revision": 2,
        },
        event_schemas.INNINGS_UPDATED: {
            "innings_id": "i",
            "runs": 10,
            "wickets": 1,
            "overs_display": "2.1",
            "digest": "d",
        },
        event_schemas.MATCH_STATE_CHANGED: {
            "from": "SCHEDULED",
            "to": "IN_PROGRESS",
            "state_title": "Day 1",
            "status_detail": None,
            "reopened": False,
            "source": "replay",
        },
        event_schemas.MATCH_UPDATED: {"fields": {"start_time_utc": "2026-01-01T00:00:00Z"}},
        event_schemas.MATCH_COMPLETED: {
            "result_type": "WIN",
            "winner_team_id": None,
            "margin_type": "runs",
            "margin_value": 5,
            "result_text": "won",
            "is_dls": False,
            "is_super_over": False,
            "digest": "d",
        },
        event_schemas.CORRECTION_APPLIED: {
            "entity_type": "delivery",
            "entity_id": "e",
            "field_path": "runs_off_bat",
            "reason": "provider revision",
            "source_revision": 1,
            "target_revision": 2,
            "result_changed": True,
        },
        event_schemas.SCORECARD_UPDATED: {
            "innings_id": None,
            "digest": "d",
            "revision": 3,
        },
    }
    assert set(samples) == event_schemas.EVENT_TYPES
    for event_type, payload in samples.items():
        body = event_schemas.envelope(event_type, "m", payload)
        jsonschema.validate(body, event_schemas.EVENT_SCHEMAS[event_type])


def test_events_since_replays_ordered(make_match) -> None:
    match = make_match()
    for n in range(3):
        event_log.append_event(
            match.id,
            event_schemas.SCORECARD_UPDATED,
            {"digest": str(n), "revision": n + 1},
        )
    result = event_log.events_since(match.id, 0)
    assert result.resync_required is False
    assert [e["seq"] for e in result.events] == [1, 2, 3]
    assert result.events[0]["payload"]["v"] == 1
    assert event_log.events_since(match.id, 3).events == []


def test_events_since_resync_paths(make_match) -> None:
    match = make_match()
    assert event_log.events_since(match.id, 0).resync_required is False
    assert event_log.events_since(match.id, 1).reason == "future_seq"
    assert event_log.events_since(match.id, -1).reason == "invalid_last_seq"
    event_log.append_event(
        match.id, event_schemas.SCORECARD_UPDATED, {"digest": "a", "revision": 1}
    )
    event_log.append_event(
        match.id, event_schemas.SCORECARD_UPDATED, {"digest": "b", "revision": 2}
    )
    assert event_log.events_since(match.id, 5).reason == "future_seq"
    # Simulate retention pruning the first event: a client at 0 cannot replay.
    MatchEventLog.objects.filter(match_id=match.id, seq=1).delete()
    assert event_log.events_since(match.id, 0).reason == "retention_window"
    assert event_log.events_since(match.id, 1).events[0]["seq"] == 2


def test_state_transition_appends_one_event(make_match) -> None:
    match = make_match()
    state.transition(match, MatchStatus.TOSS, state_title="Toss")
    rows = list(MatchEventLog.objects.filter(match_id=match.id))
    assert len(rows) == 1
    assert rows[0].event_type == event_schemas.MATCH_STATE_CHANGED
    assert rows[0].payload["from"] == "SCHEDULED"
    assert rows[0].payload["to"] == "TOSS"
    # Idempotent re-apply writes nothing.
    state.transition(match, MatchStatus.TOSS)
    assert MatchEventLog.objects.filter(match_id=match.id).count() == 1


@pytest.mark.django_db(transaction=True)
def test_redis_down_does_not_fail_ingestion(make_match, monkeypatch) -> None:
    match = make_match()
    metrics.reset()

    def boom() -> None:
        raise ConnectionError("redis down")

    monkeypatch.setattr(pub, "client", boom)
    # Publication is guarded, never raises.
    assert pub.publish_match_event(match.id, {"event_type": "x"}) is False
    row = event_log.append_event(
        match.id, event_schemas.SCORECARD_UPDATED, {"digest": "a", "revision": 1}
    )
    transaction.commit()
    assert MatchEventLog.objects.filter(pk=row.pk).exists()


def test_prune_only_terminal_matches_and_dry_run(make_match) -> None:
    live = make_match(status=MatchStatus.IN_PROGRESS)
    done = make_match(
        status=MatchStatus.COMPLETED,
        end_time_utc=timezone.now() - datetime.timedelta(days=2),
    )
    old = timezone.now() - datetime.timedelta(days=2)
    for match in (live, done):
        event_log.append_event(
            match.id, event_schemas.SCORECARD_UPDATED, {"digest": "a", "revision": 1}
        )
        MatchEventLog.objects.filter(match_id=match.id).update(created_at=old)

    report = event_log.prune_events(dry_run=True)
    assert report.dry_run is True
    assert str(done.id) in report.matches
    assert str(live.id) not in report.matches
    assert report.events == 1
    assert MatchEventLog.objects.filter(match_id=done.id).exists()

    report = event_log.prune_events(dry_run=False)
    assert report.events == 1
    assert not MatchEventLog.objects.filter(match_id=done.id).exists()
    assert MatchEventLog.objects.filter(match_id=live.id).exists()


def test_publication_channel() -> None:
    assert pub.channel_for("abc") == "match:abc"
