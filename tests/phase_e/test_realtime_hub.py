"""Realtime hub: coalescing, gap/backfill, backpressure, drain, licensing.

Driven with fakes (no Redis, no DB): a scripted :class:`FakeReader` stands in
for the event log and a :class:`FakePolicy` for licensing. Each test runs in
its own ``asyncio.run`` because the hub's ``asyncio.Queue`` instances bind to a
loop. The ≤1 Hz coalesce tick is driven explicitly (``hub.tick()``) so timing
never flakies.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from apps.core import metrics
from apps.core.streaming import StreamEvent, StreamInfo
from apps.realtime.frames import Frame, parse_frame
from apps.realtime.hub import Hub

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class FakeReader:
    """Scripted event log: ``log[match_id][seq] -> event dict``."""

    def __init__(self) -> None:
        self.log: dict[str, dict[int, dict[str, Any]]] = {}
        self.resync_on: set[str] = set()
        self.calls: list[tuple[str, int]] = []

    def events_since(self, match_id: str, last_seq: int) -> StreamInfo:
        self.calls.append((match_id, last_seq))
        if match_id in self.resync_on:
            return StreamInfo(resync_required=True, reason="retention_window")
        events = tuple(
            StreamEvent(
                seq=seq,
                event_type=data["event_type"],
                match_id=match_id,
                payload=dict(data.get("payload", {})),
                created_at=data.get("created_at", ""),
            )
            for seq, data in sorted(self.log.get(match_id, {}).items())
            if seq > last_seq
        )
        return StreamInfo(events=events)

    def latest_seq(self, match_id: str) -> int:
        return max(self.log.get(match_id, {}), default=0)


class _InclusiveReader(FakeReader):
    """Contract violation: ``events_since`` includes the boundary event.

    The hub must not assume a well-behaved reader; an event at or below the
    requested position (a race or a misbehaving implementation) is skipped,
    never re-delivered.
    """

    def events_since(self, match_id: str, last_seq: int) -> StreamInfo:
        self.calls.append((match_id, last_seq))
        if match_id in self.resync_on:
            return StreamInfo(resync_required=True, reason="retention_window")
        events = tuple(
            StreamEvent(
                seq=seq,
                event_type=data["event_type"],
                match_id=match_id,
                payload=dict(data.get("payload", {})),
                created_at=data.get("created_at", ""),
            )
            for seq, data in sorted(self.log.get(match_id, {}).items())
            if seq >= last_seq
        )
        return StreamInfo(events=events)


class FakePolicy:
    def __init__(self, allowed: frozenset[str] | None = None) -> None:
        self._allowed = allowed
        self.calls: list[str] = []

    def allowed_event_types(self, match_id: str) -> frozenset[str] | None:
        self.calls.append(match_id)
        return self._allowed


def _frame(match_id: str, seq: int, event_type: str = "delivery.appended", **payload: Any) -> Frame:
    return Frame(
        seq=seq,
        event_type=event_type,
        match_id=match_id,
        payload={"v": 1, "match_id": match_id, **payload},
    )


async def _drain_queue(queue: asyncio.Queue[Any]) -> list[Frame]:
    frames: list[Frame] = []
    while not queue.empty():
        frames.append(await queue.get())
    return frames


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Subscribe / replay
# --------------------------------------------------------------------------- #


def test_live_subscriber_receives_frames_on_tick() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        sub = await hub.subscribe("m-1")
        await hub.publish(_frame("m-1", 1))
        await hub.publish(_frame("m-1", 2))
        assert sub.queue.empty()  # coalescing: nothing before the tick
        await hub.tick()
        frames = await _drain_queue(sub.queue)
        assert len(frames) == 1
        assert frames[0].event_type == "batch"
        assert frames[0].payload["count"] == 2
        assert sub.last_seq == 2

    _run(scenario())


def test_single_pending_frame_is_not_wrapped_in_batch() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        sub = await hub.subscribe("m-1")
        await hub.publish(_frame("m-1", 1))
        await hub.tick()
        frames = await _drain_queue(sub.queue)
        assert len(frames) == 1
        assert frames[0].event_type == "delivery.appended"
        assert frames[0].seq == 1

    _run(scenario())


def test_subscribe_with_last_seq_replays_committed_events() -> None:
    async def scenario() -> None:
        reader = FakeReader()
        reader.log["m-1"] = {
            1: {"event_type": "delivery.appended"},
            2: {"event_type": "delivery.appended"},
            3: {"event_type": "match.state_changed"},
        }
        hub = Hub(reader=reader)
        sub = await hub.subscribe("m-1", last_seq=1)
        frames = await _drain_queue(sub.queue)
        assert [f.seq for f in frames] == [2, 3]
        assert sub.last_seq == 3

    _run(scenario())


def test_subscribe_replay_does_not_duplicate_pending_frames() -> None:
    async def scenario() -> None:
        reader = FakeReader()
        reader.log["m-1"] = {1: {"event_type": "delivery.appended"}}
        hub = Hub(reader=reader)
        await hub.subscribe("m-1")  # live subscriber, fanout exists
        await hub.publish(_frame("m-1", 1))  # pending, not yet committed to reader
        late = await hub.subscribe("m-1", last_seq=0)
        # Reader has 1 committed: replay delivers it; the pending copy is a dup.
        frames = await _drain_queue(late.queue)
        assert [f.seq for f in frames] == [1]
        await hub.tick()
        assert late.queue.empty()  # the pending 1 was deduplicated

    _run(scenario())


def test_subscribe_with_unreplayable_position_sends_resync() -> None:
    async def scenario() -> None:
        reader = FakeReader()
        reader.resync_on.add("m-1")
        hub = Hub(reader=reader)
        sub = await hub.subscribe("m-1", last_seq=50)
        frames = await _drain_queue(sub.queue)
        assert len(frames) == 1
        assert frames[0].event_type == "resync_required"
        assert frames[0].payload["reason"] == "retention_window"
        assert "realtime_resyncs_total" in metrics.render()

    _run(scenario())


# --------------------------------------------------------------------------- #
# Coalescing + immediate flush (ledger #6)
# --------------------------------------------------------------------------- #


def test_coalescing_batches_rapid_deliveries_into_one_frame() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        sub = await hub.subscribe("m-1")
        for seq in range(1, 11):
            await hub.publish(_frame("m-1", seq))
        await hub.tick()
        frames = await _drain_queue(sub.queue)
        assert len(frames) == 1  # ≤1 Hz: one update per client
        assert frames[0].event_type == "batch"
        assert frames[0].payload["count"] == 10
        assert "realtime_coalesced_batches_total" in metrics.render()

    _run(scenario())


def test_correction_flushes_immediately_without_waiting() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        sub = await hub.subscribe("m-1")
        await hub.publish(_frame("m-1", 1, "delivery.appended"))
        await hub.publish(_frame("m-1", 2, "correction.applied", digest="d"))
        # No tick: pending 1 must have flushed when the correction arrived.
        frames = await _drain_queue(sub.queue)
        assert [f.seq for f in frames] == [1, 2]
        assert frames[1].event_type == "correction.applied"

    _run(scenario())


def test_match_completed_flushes_immediately() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        sub = await hub.subscribe("m-1")
        await hub.publish(_frame("m-1", 1, "innings.updated"))
        await hub.publish(_frame("m-1", 2, "match.completed"))
        frames = await _drain_queue(sub.queue)
        assert [f.seq for f in frames] == [1, 2]

    _run(scenario())


# --------------------------------------------------------------------------- #
# Gap / backfill (ledger #4)
# --------------------------------------------------------------------------- #


def test_gap_backfill_replays_missing_run_in_order() -> None:
    async def scenario() -> None:
        reader = FakeReader()
        reader.log["m-1"] = {
            2: {"event_type": "delivery.appended", "payload": {"delivery_seq": 2}},
            3: {"event_type": "delivery.appended", "payload": {"delivery_seq": 3}},
        }
        hub = Hub(reader=reader)
        sub = await hub.subscribe("m-1")
        await hub.publish(_frame("m-1", 1))  # pending
        await hub.publish(_frame("m-1", 4))  # gap: 4 > 1+1 -> backfill 2,3
        frames = await _drain_queue(sub.queue)
        assert [f.seq for f in frames] == [1, 2, 3]
        assert frames[1].payload["delivery_seq"] == 2  # replayed from the log
        assert (hub.highest_seq("m-1")) == 4
        await hub.tick()
        assert [f.seq for f in await _drain_queue(sub.queue)] == [4]
        assert "realtime_gap_backfills_total" in metrics.render()

    _run(scenario())


def test_gap_backfill_resync_sends_resync_frame_and_continues_live() -> None:
    async def scenario() -> None:
        reader = FakeReader()
        reader.resync_on.add("m-1")
        hub = Hub(reader=reader)
        sub = await hub.subscribe("m-1")
        await hub.publish(_frame("m-1", 1))
        await hub.publish(_frame("m-1", 5))  # gap; backfill says unreplayable
        # Pending 1 flushed first, then the resync notice (seq = highest seen).
        frames = await _drain_queue(sub.queue)
        assert [f.event_type for f in frames] == ["delivery.appended", "resync_required"]
        assert frames[-1].payload["reason"] == "retention_window"
        assert sub.last_seq == 1  # the notice did not advance the position
        await hub.tick()
        rest = await _drain_queue(sub.queue)
        assert [f.event_type for f in rest] == ["delivery.appended"]
        assert rest[0].seq == 5
        # Live delivery continues without re-querying the pruned log.
        await hub.publish(_frame("m-1", 6))
        await hub.tick()
        assert [f.seq for f in await _drain_queue(sub.queue)] == [6]
        assert reader.calls.count(("m-1", 1)) == 1

    _run(scenario())


# --------------------------------------------------------------------------- #
# Backpressure (ledger #8)
# --------------------------------------------------------------------------- #


def test_slow_consumer_drops_are_counted_and_recoverable() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader(), queue_capacity=1)
        sub = await hub.subscribe("m-1")
        await hub.publish(_frame("m-1", 1))
        await hub.tick()  # queue now holds frame 1 (client never reads)
        await hub.publish(_frame("m-1", 2))
        await hub.tick()  # queue full -> dropped
        assert sub.last_seq == 1  # position did not advance past the drop
        assert "realtime_dropped_frames_total" in metrics.render()
        # Once the client catches up, the next tick is fine again.
        first = await sub.queue.get()
        assert first.seq == 1
        await hub.publish(_frame("m-1", 3))
        await hub.tick()
        frames = await _drain_queue(sub.queue)
        assert [f.seq for f in frames] == [3]
        assert sub.last_seq == 3

    _run(scenario())


# --------------------------------------------------------------------------- #
# Licensing degradation (SECURITY §11)
# --------------------------------------------------------------------------- #


def test_licensing_policy_filters_ball_events_but_tracks_seq() -> None:
    async def scenario() -> None:
        policy = FakePolicy(
            allowed=frozenset({"innings.updated", "match.completed", "scorecard.updated"})
        )
        hub = Hub(reader=FakeReader(), policy=policy)
        sub = await hub.subscribe("m-1")
        await hub.publish(_frame("m-1", 1, "delivery.appended"))  # filtered
        await hub.publish(_frame("m-1", 2, "innings.updated"))  # allowed
        await hub.tick()
        frames = await _drain_queue(sub.queue)
        assert [f.seq for f in frames] == [2]
        assert hub.highest_seq("m-1") == 2  # sequence still consistent
        assert policy.calls == ["m-1"]

    _run(scenario())


def test_policy_resolved_once_per_fanout() -> None:
    async def scenario() -> None:
        policy = FakePolicy(allowed=None)
        hub = Hub(reader=FakeReader(), policy=policy)
        await hub.subscribe("m-1")
        await hub.subscribe("m-1")
        assert policy.calls == ["m-1"]  # resolved when the fanout was created

    _run(scenario())


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


def test_unsubscribe_removes_subscriber_and_cleans_fanout() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        sub_a = await hub.subscribe("m-1")
        sub_b = await hub.subscribe("m-1")
        assert hub.subscriber_count() == 2
        hub.unsubscribe(sub_a)
        assert hub.subscriber_count() == 1
        await hub.publish(_frame("m-1", 1))
        await hub.tick()
        assert sub_b.queue.qsize() == 1
        assert sub_a.queue.empty()
        hub.unsubscribe(sub_b)
        assert hub.active_match_ids() == []
        assert hub.subscriber_count() == 0
        # A publish to a match with no subscribers is a no-op (no crash).
        await hub.publish(_frame("m-1", 2))

    _run(scenario())


def test_publish_for_unsubscribed_match_is_ignored() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        await hub.publish(_frame("m-9", 1))  # no fanout
        assert hub.active_match_ids() == []

    _run(scenario())


def test_drain_flushes_notifies_and_closes_all() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        sub_a = await hub.subscribe("m-1")
        sub_b = await hub.subscribe("m-2")
        await hub.publish(_frame("m-1", 1, "delivery.appended"))
        await hub.publish(_frame("m-1", 2, "delivery.appended"))
        await hub.drain(grace=0.0, jitter=0.0, reason="deploy")
        a_frames = await _drain_queue(sub_a.queue)
        assert a_frames[-1].event_type == "server.draining"
        assert a_frames[-1].payload["reason"] == "deploy"
        b_frames = await _drain_queue(sub_b.queue)
        assert b_frames[-1].event_type == "server.draining"
        assert sub_a.closed and sub_b.closed
        assert hub.active_match_ids() == []
        assert "realtime_drains_total" in metrics.render()

    _run(scenario())


def test_drain_delivers_coalesced_batch_before_draining_notice() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        sub = await hub.subscribe("m-1")
        await hub.publish(_frame("m-1", 1))
        await hub.publish(_frame("m-1", 2))
        await hub.drain(grace=0.0, jitter=0.0)
        frames = await _drain_queue(sub.queue)
        assert frames[-1].event_type == "server.draining"
        assert frames[-2].event_type == "batch"

    _run(scenario())


def test_duplicate_frames_are_skipped() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        sub = await hub.subscribe("m-1")
        await hub.publish(_frame("m-1", 1))
        await hub.tick()
        await hub.publish(_frame("m-1", 1))  # broker redelivered
        await hub.tick()
        frames = await _drain_queue(sub.queue)
        assert [f.seq for f in frames] == [1]

    _run(scenario())


def test_backfill_skips_events_already_seen() -> None:
    async def scenario() -> None:
        reader = FakeReader()
        # The reader returns a stale event (≤ highest): a race where the same
        # event was also delivered live. It must be skipped, not replayed.
        reader.log["m-1"] = {2: {"event_type": "delivery.appended"}}
        hub = Hub(reader=reader)
        sub = await hub.subscribe("m-1")
        await hub.publish(_frame("m-1", 1))
        await hub.publish(_frame("m-1", 3))  # gap; backfill returns 2
        frames = await _drain_queue(sub.queue)
        assert [f.seq for f in frames] == [1, 2]
        await hub.tick()
        assert [f.seq for f in await _drain_queue(sub.queue)] == [3]

    _run(scenario())


def test_backfill_applies_licensing_filter_to_replayed_events() -> None:
    async def scenario() -> None:
        reader = FakeReader()
        reader.log["m-1"] = {2: {"event_type": "delivery.appended"}}
        policy = FakePolicy(allowed=frozenset({"innings.updated"}))
        hub = Hub(reader=reader, policy=policy)
        sub = await hub.subscribe("m-1")
        await hub.publish(_frame("m-1", 1, "delivery.appended"))
        await hub.publish(_frame("m-1", 3, "innings.updated"))  # gap
        # Both deliveries were licence-filtered (live 1 and replayed 2); the
        # sequence still advanced through them so live 3 is contiguous.
        assert sub.queue.empty()
        await hub.tick()
        rest = await _drain_queue(sub.queue)
        assert [f.event_type for f in rest] == ["innings.updated"]
        assert hub.highest_seq("m-1") == 3

    _run(scenario())


def test_unsubscribing_last_client_removes_fanout() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        sub = await hub.subscribe("m-1")
        await hub.publish(_frame("m-1", 1))
        await hub.publish(_frame("m-1", 2))
        hub.unsubscribe(sub)
        assert hub.active_match_ids() == []
        assert hub.highest_seq("m-1") == 0
        assert hub.subscriber_count_for("m-1") == 0

    _run(scenario())


def test_fanout_flush_with_no_subscribers_drops_pending() -> None:
    """Defensive branch: pending work with no subscribers is dropped, not kept."""

    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        sub = await hub.subscribe("m-1")
        await hub.publish(_frame("m-1", 1))
        await hub.publish(_frame("m-1", 2))
        fanout = hub._fanouts["m-1"]
        fanout.remove(sub)
        assert fanout.pending
        await fanout._flush_pending()
        assert fanout.pending == {}
        assert sub.queue.empty()

    _run(scenario())


def test_publish_to_closed_subscription_is_dropped() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        sub = await hub.subscribe("m-1")
        await hub.drain(grace=0.0, jitter=0.0)
        # A straggling broker message after drain must not raise.
        await hub.publish(_frame("m-1", 9))
        assert sub.closed is True

    _run(scenario())


def test_unsubscribe_after_drain_is_safe() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        sub = await hub.subscribe("m-1")
        await hub.drain(grace=0.0, jitter=0.0)
        hub.unsubscribe(sub)  # fanout already gone
        assert hub.active_match_ids() == []
        assert hub.subscriber_count() == 0
        assert hub.subscriber_count_for("m-1") == 0

    _run(scenario())


def test_ticker_delivers_coalesced_frames() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader(), flush_interval=0.01)
        sub = await hub.subscribe("m-1")
        hub.start()
        try:
            await hub.publish(_frame("m-1", 1))
            await hub.publish(_frame("m-1", 2))
            # Real time: the ticker sleeps flush_interval between ticks.
            await asyncio.sleep(0.06)
        finally:
            await hub.stop()
        assert sub.last_seq == 2
        frames = await _drain_queue(sub.queue)
        assert frames[0].event_type == "batch"

    _run(scenario())


def test_drain_with_grace_and_jitter_still_notifies() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        sub = await hub.subscribe("m-1")
        await hub.publish(_frame("m-1", 1))
        await hub.drain(grace=0.02, jitter=0.01)
        frames = await _drain_queue(sub.queue)
        assert frames[-1].event_type == "server.draining"
        assert sub.closed is True

    _run(scenario())


def test_broker_json_message_routes_through_hub() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        sub = await hub.subscribe("m-1")
        raw = json.dumps(
            {
                "seq": 1,
                "event_type": "delivery.appended",
                "match_id": "m-1",
                "created_at": "2026-11-04T10:00:00Z",
            }
        )
        await hub.publish(parse_frame(raw))
        await hub.tick()
        frames = await _drain_queue(sub.queue)
        assert frames[0].seq == 1

    _run(scenario())


def test_put_to_closed_subscription_is_dropped() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        sub = await hub.subscribe("m-1")
        sub.close()
        assert await sub.put(_frame("m-1", 1)) is False
        assert sub.queue.empty()

    _run(scenario())


def test_backfill_skips_stale_events_at_or_below_highest() -> None:
    async def scenario() -> None:
        reader = _InclusiveReader()
        reader.log["m-1"] = {1: {"event_type": "delivery.appended"}}
        hub = Hub(reader=reader)
        sub = await hub.subscribe("m-1")
        await hub.publish(_frame("m-1", 1))  # highest = 1
        await hub.publish(_frame("m-1", 3))  # gap -> backfill
        # The misbehaving reader returns seq 1 (the boundary) for last_seq=1:
        # it is not > highest, so the hub skips it instead of re-delivering.
        frames = await _drain_queue(sub.queue)
        assert [f.seq for f in frames] == [1]  # pending 1 flushed at the gap
        await hub.tick()
        assert [f.seq for f in await _drain_queue(sub.queue)] == [3]
        assert hub.highest_seq("m-1") == 3

    _run(scenario())


def test_replay_skips_stale_events_at_or_below_last_seq() -> None:
    async def scenario() -> None:
        reader = _InclusiveReader()
        reader.log["m-1"] = {
            1: {"event_type": "delivery.appended"},
            2: {"event_type": "delivery.appended"},
        }
        hub = Hub(reader=reader)
        sub = await hub.subscribe("m-1", last_seq=1)
        # events_since(m-1, 1) returns seq 1 (boundary, already consumed) and 2;
        # only the event after the client's position is re-delivered.
        frames = await _drain_queue(sub.queue)
        assert [f.seq for f in frames] == [2]
        assert sub.last_seq == 2

    _run(scenario())


def test_start_is_idempotent() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        hub.start()
        first = hub._ticker
        hub.start()  # already running: no second ticker task
        assert hub._ticker is first
        await hub.stop()
        assert hub._ticker is None

    _run(scenario())
