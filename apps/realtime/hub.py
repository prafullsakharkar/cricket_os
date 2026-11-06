"""Per-match subscriber hub: coalescing, backpressure, gap backfill, drain.

The hub is the in-process fan-out point on every web node. The broker (Redis
pub/sub, or degraded polling) feeds it live frames; it routes them to the
per-match fanout, which delivers to subscribers. Behaviours (API_SPEC §3.8,
PERFORMANCE §8, Phase E ledger decisions 5–8):

* **Coalescing** — ordinary deltas accumulate in ``pending`` and flush as one
  ``batch`` frame on the ≤1 Hz tick, so a burst of deliveries costs one
  update per client;
* **Immediate flush** — corrections and terminal transitions never wait for
  the tick (ledger #6);
* **Backpressure** — every subscriber has a bounded queue; a slow consumer
  whose queue is full has the batch dropped and counted (recovery is the
  seq-based backfill on reconnect, so nothing is permanently lost);
* **Gap/backfill** — a live frame whose seq skips the expected next seq means
  pub/sub dropped messages; the hub flushes pending, replays the missing run
  from the log reader, then continues. An unreplayable position yields
  ``resync_required``;
* **Licensing degradation** — per-match policy filters unlicensed event types
  (sequence is still tracked, delivery is not);
* **Drain** — on shutdown, pending work is flushed, a ``server.draining``
  notice goes to every subscriber, and connections close with a small jittered
  stagger so reconnects do not thundering-herd.

The hub is single-threaded (one asyncio loop per worker) so it needs no locks.
The reader (sync, ORM-backed) is called directly; replay is rare and indexed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import uuid

from apps.core import metrics
from apps.core.streaming import EventLogReader, MatchPolicy
from apps.realtime.frames import (
    Frame,
    control_frame,
    from_stream_event,
    is_immediate,
    is_notice,
    make_batch,
)

logger = logging.getLogger("apps.realtime.hub")

#: Default per-subscriber queue capacity. With ≤1 Hz coalescing this buffers
#: minutes of updates; a client that cannot keep up gets drops, not memory.
DEFAULT_QUEUE_CAPACITY = 64


class Subscription:
    """One client connection to one match."""

    def __init__(self, match_id: str, *, queue_capacity: int) -> None:
        self.id = str(uuid.uuid4())
        self.match_id = match_id
        self.queue: asyncio.Queue[Frame] = asyncio.Queue(maxsize=queue_capacity)
        #: Highest seq delivered to this client (0 = joined live).
        self.last_seq = 0
        self.closed = False

    async def put(self, frame: Frame) -> bool:
        """Deliver ``frame``; ``False`` when dropped (duplicate/full/closed).

        Notices (``resync_required``/``server.draining``) never advance
        ``last_seq``; every other frame — including a coalesced ``batch`` —
        does.
        """
        if self.closed:
            return False
        if not is_notice(frame.event_type) and frame.seq <= self.last_seq:
            return False  # duplicate (replay raced a pending tick)
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            metrics.REALTIME_DROPPED.inc()
            return False
        if not is_notice(frame.event_type):
            self.last_seq = frame.seq
        metrics.REALTIME_FANOUT_FRAMES.inc()
        return True

    def close(self) -> None:
        self.closed = True


class _Fanout:
    """The subscriber group + pending/coalescing state for one match."""

    def __init__(self, hub: Hub, match_id: str, *, allowed: frozenset[str] | None) -> None:
        self.hub = hub
        self.match_id = match_id
        #: Licensed event types (None = everything allowed).
        self._allowed = allowed
        self.subscribers: set[Subscription] = set()
        self.pending: dict[int, Frame] = {}
        self.highest_seq = 0
        #: A backfill reported the retained window was unreplayable; stop gap
        #: re-querying and keep delivering live (clients refetch via resync).
        self.resync_notified = False

    def add(self, sub: Subscription) -> None:
        self.subscribers.add(sub)

    def remove(self, sub: Subscription) -> None:
        self.subscribers.discard(sub)

    def _permits(self, event_type: str) -> bool:
        return self._allowed is None or event_type in self._allowed

    async def ingest(self, frame: Frame) -> None:
        """Accept one live frame: order, filter, coalesce or flush."""
        if frame.seq > self.highest_seq + 1 and not self.resync_notified:
            await self._flush_pending()
            await self._backfill()
        self.highest_seq = max(self.highest_seq, frame.seq)
        if not self._permits(frame.event_type):
            return  # licence-filtered: tracked, never delivered
        if is_immediate(frame.event_type):
            await self._flush_pending()
            await self._deliver_all(frame)
        else:
            self.pending[frame.seq] = frame

    async def _backfill(self) -> None:
        """Replay the missing run after a pub/sub gap (ledger #4)."""
        metrics.REALTIME_GAP_BACKFILLS.inc()
        info = await asyncio.to_thread(
            self.hub.reader.events_since, self.match_id, self.highest_seq
        )
        if info.resync_required:
            self.resync_notified = True
            metrics.REALTIME_RESYNCS.inc()
            await self._deliver_all(
                control_frame(
                    "resync_required",
                    self.match_id,
                    seq=self.highest_seq,
                    reason=info.reason or "unreplayable_position",
                )
            )
            return
        for event in info.events:
            if event.seq > self.highest_seq:
                self.highest_seq = event.seq
                if self._permits(event.event_type):
                    await self._deliver_all(from_stream_event(event))

    async def _flush_pending(self) -> None:
        """Coalesce pending frames into one batch and deliver (≤1 Hz)."""
        if not self.pending:
            return
        frames = [self.pending[seq] for seq in sorted(self.pending)]
        self.pending.clear()
        if not self.subscribers:
            return
        if len(frames) == 1:
            await self._deliver_all(frames[0])
        else:
            metrics.REALTIME_COALESCED.inc()
            await self._deliver_all(make_batch(self.match_id, frames))

    async def _deliver_all(self, frame: Frame) -> None:
        for sub in list(self.subscribers):
            await sub.put(frame)


class Hub:
    """Routes frames to per-match fanouts and manages subscriber lifecycles."""

    def __init__(
        self,
        *,
        reader: EventLogReader | None = None,
        policy: MatchPolicy | None = None,
        flush_interval: float = 1.0,
        queue_capacity: int = DEFAULT_QUEUE_CAPACITY,
    ) -> None:
        self._reader_override = reader
        self._policy_override = policy
        #: Coalesce window in seconds (the ≤1 Hz tick interval).
        self.flush_interval = flush_interval
        self.queue_capacity = queue_capacity
        self._fanouts: dict[str, _Fanout] = {}
        self._ticker: asyncio.Task[None] | None = None

    # -- wiring ------------------------------------------------------------ #

    @property
    def reader(self) -> EventLogReader:
        if self._reader_override is not None:
            return self._reader_override
        from apps.realtime import wiring

        return wiring.get_reader()

    @property
    def policy(self) -> MatchPolicy | None:
        if self._policy_override is not None:
            return self._policy_override
        from apps.realtime import wiring

        return wiring.get_policy()

    # -- subscriber lifecycle ---------------------------------------------- #

    async def subscribe(self, match_id: str, *, last_seq: int | None = None) -> Subscription:
        """Connect a client. ``last_seq`` triggers a replay (recovery)."""
        fanout = self._fanouts.get(match_id)
        if fanout is None:
            allowed = None
            policy = self.policy
            if policy is not None:
                # Policy resolution touches the ORM (licensing); run it off the
                # event loop (Django forbids sync ORM calls from async code).
                allowed = await asyncio.to_thread(policy.allowed_event_types, match_id)
            fanout = _Fanout(self, match_id, allowed=allowed)
            self._fanouts[match_id] = fanout
        sub = Subscription(match_id, queue_capacity=self.queue_capacity)
        if last_seq is not None:
            # The client's stated position is authoritative from the start, so
            # replay dedupe and live delivery never regress below it.
            sub.last_seq = last_seq
        fanout.add(sub)
        metrics.REALTIME_CONNECTIONS.set(self.subscriber_count())
        if last_seq is not None:
            await self._replay(sub, match_id, last_seq)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        fanout = self._fanouts.get(sub.match_id)
        if fanout is not None:
            fanout.remove(sub)
            if not fanout.subscribers:
                del self._fanouts[sub.match_id]
        metrics.REALTIME_CONNECTIONS.set(self.subscriber_count())

    async def _replay(self, sub: Subscription, match_id: str, last_seq: int) -> None:
        # The reader is sync ORM; Django forbids that from async code, so run
        # the read off the event loop (replay is rare and single-row-indexed).
        info = await asyncio.to_thread(self.reader.events_since, match_id, last_seq)
        if info.resync_required:
            metrics.REALTIME_RESYNCS.inc()
            await sub.put(
                control_frame(
                    "resync_required",
                    match_id,
                    seq=last_seq,
                    reason=info.reason or "unreplayable_position",
                )
            )
            return
        for event in info.events:
            if event.seq > sub.last_seq:
                await sub.put(from_stream_event(event))

    # -- frame ingestion ---------------------------------------------------- #

    async def publish(self, frame: Frame) -> None:
        """Route one live frame (from the broker) to its match fanout."""
        fanout = self._fanouts.get(frame.match_id)
        if fanout is None:
            return  # nobody subscribed: nothing to deliver
        await fanout.ingest(frame)

    async def tick(self) -> None:
        """The ≤1 Hz coalesce tick (driven by the ticker or a test directly)."""
        for fanout in list(self._fanouts.values()):
            await fanout._flush_pending()

    def start(self) -> None:
        """Start the coalesce ticker (spawned by the broker's ``run``)."""
        if self._ticker is None:
            self._ticker = asyncio.create_task(self._tick_loop())

    async def stop(self) -> None:
        """Stop the coalesce ticker (pending frames are left for the caller)."""
        if self._ticker is not None:
            self._ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ticker
            self._ticker = None

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(max(0.0, self.flush_interval))
            await self.tick()

    # -- observability / introspection -------------------------------------- #

    def active_match_ids(self) -> list[str]:
        """Matches with at least one subscriber (drives degraded polling)."""
        return sorted(self._fanouts)

    def highest_seq(self, match_id: str) -> int:
        fanout = self._fanouts.get(match_id)
        return fanout.highest_seq if fanout is not None else 0

    def subscriber_count(self) -> int:
        return sum(len(f.subscribers) for f in self._fanouts.values())

    def subscriber_count_for(self, match_id: str) -> int:
        fanout = self._fanouts.get(match_id)
        return len(fanout.subscribers) if fanout is not None else 0

    # -- drain --------------------------------------------------------------- #

    async def drain(
        self,
        *,
        grace: float = 2.0,
        jitter: float = 0.1,
        reason: str = "draining",
    ) -> None:
        """Graceful shutdown: flush, notify, then close with a staggered jitter.

        ``grace`` lets consumers drain their queues; ``jitter`` staggers
        connection closes so reconnecting clients do not thundering-herd.
        """
        await self.stop()  # no more coalesce ticks during shutdown
        metrics.REALTIME_DRAINS.inc()
        logger.info(
            "realtime hub draining",
            extra={"reason": reason, "matches": len(self._fanouts)},
        )
        for fanout in list(self._fanouts.values()):
            await fanout._flush_pending()
            await fanout._deliver_all(
                control_frame(
                    "server.draining",
                    fanout.match_id,
                    seq=fanout.highest_seq,
                    reason=reason,
                )
            )
        if grace > 0:
            await asyncio.sleep(grace)
        for fanout in list(self._fanouts.values()):
            for sub in list(fanout.subscribers):
                if jitter > 0:
                    await asyncio.sleep(random.uniform(0, jitter))
                fanout.remove(sub)
                sub.close()
        self._fanouts.clear()
        metrics.REALTIME_CONNECTIONS.set(0)
        logger.info("realtime hub drained", extra={"reason": reason})


__all__ = ["Hub", "Subscription"]
