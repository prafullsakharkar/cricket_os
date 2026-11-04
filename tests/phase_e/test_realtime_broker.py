"""Realtime broker: pub/sub -> hub bridge, degraded polling, recovery.

The transport is a fake (no network): a scripted message queue and a failure
switch. Degraded mode polls the injected reader for every match with
subscribers; a successful ``ping`` restores pub/sub delivery.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from apps.core import metrics
from apps.realtime.broker import Broker
from apps.realtime.hub import Hub

from .test_realtime_hub import FakeReader

pytestmark = pytest.mark.django_db


class FakeTransport:
    def __init__(
        self,
        *,
        messages: list[str] | None = None,
        fail: bool = False,
        ping_ok: bool = True,
    ) -> None:
        self._queue = list(messages or [])
        self.fail = fail
        self.ping_ok = ping_ok
        self.calls: list[tuple[str, Any]] = []
        self.closed = False

    def subscribe(self, pattern: str) -> None:
        self.calls.append(("subscribe", pattern))

    def messages(self, timeout: float | None = 0.0) -> list[str]:
        self.calls.append(("messages", timeout))
        if self.fail:
            raise ConnectionError("redis down")
        return [self._queue.pop(0)] if self._queue else []

    def ping(self) -> bool:
        self.calls.append(("ping",))
        return self.ping_ok

    def close(self) -> None:
        self.closed = True
        self.calls.append(("close",))


def _raw(match_id: str, seq: int, event_type: str = "delivery.appended") -> str:
    return json.dumps(
        {
            "seq": seq,
            "event_type": event_type,
            "match_id": match_id,
            "created_at": "2026-11-04T10:00:00Z",
            "v": 1,
        }
    )


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _drain(queue: asyncio.Queue[Any]) -> list[Any]:
    frames = []
    while not queue.empty():
        frames.append(await queue.get())
    return frames


def test_process_routes_valid_message_to_subscribers() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        sub = await hub.subscribe("m-1")
        broker = Broker(hub, transport=FakeTransport())
        await broker.process(_raw("m-1", 1))
        await broker.process(_raw("m-1", 2))
        await hub.tick()
        frames = await _drain(sub.queue)
        assert frames[0].event_type == "batch"
        assert frames[0].payload["count"] == 2
        assert sub.last_seq == 2

    _run(scenario())


def test_invalid_messages_are_dropped_and_counted() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        sub = await hub.subscribe("m-1")
        broker = Broker(hub, transport=FakeTransport())
        await broker.process("not json")
        await broker.process('{"seq": 1}')  # missing match_id/event_type
        await broker.process(_raw("m-1", 1))
        await hub.tick()
        assert sub.queue.qsize() == 1
        assert "realtime_invalid_frames_total" in metrics.render()

    _run(scenario())


def test_degrades_after_max_errors() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        transport = FakeTransport(fail=True)
        broker = Broker(hub, transport=transport, max_errors=3)
        assert broker.degraded is False
        for _ in range(2):
            await broker._pubsub_step()
        assert broker.degraded is False
        await broker._pubsub_step()
        assert broker.degraded is True
        assert "realtime_degraded 1" in metrics.render()

    _run(scenario())


def test_recovers_when_ping_succeeds() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        transport = FakeTransport(fail=True, ping_ok=True)
        broker = Broker(hub, transport=transport, max_errors=1)
        await broker._pubsub_step()
        assert broker.degraded is True
        transport.fail = False
        await broker._degraded_step()
        assert broker.degraded is False
        assert "realtime_degraded 0" in metrics.render()

    _run(scenario())


def test_degraded_poll_once_replays_log_events_for_active_matches() -> None:
    async def scenario() -> None:
        reader = FakeReader()
        reader.log["m-1"] = {
            1: {"event_type": "delivery.appended", "payload": {"delivery_seq": 1}},
            2: {"event_type": "delivery.appended", "payload": {"delivery_seq": 2}},
        }
        hub = Hub(reader=reader)
        sub = await hub.subscribe("m-1")
        broker = Broker(hub, reader=reader, transport=FakeTransport())
        await broker.poll_once()
        await hub.tick()  # the run loop ticks; poll_once only feeds the hub
        frames = await _drain(sub.queue)
        assert [f.seq for f in frames] == [2]  # coalesced batch (1+2)
        assert frames[0].payload["count"] == 2
        assert hub.highest_seq("m-1") == 2

    _run(scenario())


def test_poll_once_skips_matches_without_subscribers() -> None:
    async def scenario() -> None:
        reader = FakeReader()
        reader.log["m-9"] = {1: {"event_type": "delivery.appended"}}
        hub = Hub(reader=reader)
        broker = Broker(hub, reader=reader, transport=FakeTransport())
        await broker.poll_once()  # no fanout -> no crash, no work
        assert hub.active_match_ids() == []

    _run(scenario())


def test_full_run_loop_subscribes_and_delivers() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        sub = await hub.subscribe("m-1")
        transport = FakeTransport(messages=[_raw("m-1", 1), _raw("m-1", 2)])
        broker = Broker(hub, transport=transport)
        task = asyncio.create_task(broker.run())
        # Let the loop consume the two queued messages, then stop it.
        for _ in range(20):
            await asyncio.sleep(0)
            if not transport._queue:
                break
        await broker.stop()
        await task
        assert ("subscribe", "match:*") in transport.calls
        # The two messages coalesce into one ≤1 Hz batch (tick driven here for
        # determinism; the broker's ticker normally does it).
        await hub.tick()
        frames = await _drain(sub.queue)
        assert [f.seq for f in frames] == [2]
        assert frames[0].payload["count"] == 2
        assert transport.closed is True

    _run(scenario())


def test_run_degrades_when_subscribe_fails() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())

        class _BrokenTransport(FakeTransport):
            def subscribe(self, pattern: str) -> None:
                raise ConnectionError("redis down")

            def ping(self) -> bool:
                return False  # never recovers

        broker = Broker(hub, transport=_BrokenTransport(), max_errors=1)
        task = asyncio.create_task(broker.run())
        await asyncio.sleep(0)
        await broker.stop()
        await task
        assert broker.degraded is True

    _run(scenario())


def test_run_degraded_mode_polls_then_recovers() -> None:
    async def scenario() -> None:
        reader = FakeReader()
        reader.log["m-1"] = {
            1: {"event_type": "delivery.appended", "payload": {"delivery_seq": 1}}
        }
        hub = Hub(reader=reader)
        sub = await hub.subscribe("m-1")

        class _FlakyTransport(FakeTransport):
            def __init__(self) -> None:
                super().__init__(fail=True, ping_ok=False)
                self.failures = 0

            def messages(self, timeout: float | None = 0.0) -> list[str]:
                self.failures += 1
                raise ConnectionError("redis down")

            def ping(self) -> bool:
                return False  # stays degraded for the first poll cycle

        transport = _FlakyTransport()
        broker = Broker(
            hub,
            reader=reader,
            transport=transport,
            max_errors=1,
            poll_interval=0.01,
        )
        task = asyncio.create_task(broker.run())
        # Let the loop degrade, then run a degraded poll cycle (the poll feeds
        # the hub, advancing its highest seq — that is our readiness signal).
        for _ in range(200):
            await asyncio.sleep(0)
            if hub.highest_seq("m-1") >= 1:
                break
        await broker.stop()
        await task
        assert broker.degraded is True  # never recovered while ping failed
        assert "realtime_degraded 1" in metrics.render()
        # The degraded poll fed the hub; the final tick delivers the frame.
        await hub.tick()
        frames = await _drain(sub.queue)
        assert frames and frames[-1].seq == 1

    _run(scenario())


def test_redis_transport_with_fake_client() -> None:
    """The real Redis wrapper driven by a scripted fake client (no network)."""

    class _FakePubSub:
        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = [
                {"type": "pmessage", "data": _raw("m-1", 1)}
            ]
            self.closed = False

        def psubscribe(self, pattern: str) -> None:
            self.pattern = pattern

        def get_message(self, ignore_subscribe_messages: bool = True, timeout: float | None = 0.0) -> object:
            return self.messages.pop(0) if self.messages else None

        def close(self) -> None:
            self.closed = True

    class _FakeClient:
        def __init__(self) -> None:
            self.pubsubs: list[_FakePubSub] = []
            self.pinged = 0

        def pubsub(self) -> _FakePubSub:
            ps = _FakePubSub()
            self.pubsubs.append(ps)
            return ps

        def ping(self) -> bool:
            self.pinged += 1
            return True

    from apps.realtime.broker import RedisPubSubTransport

    client = _FakeClient()
    transport = RedisPubSubTransport(client=client)
    transport.subscribe("match:*")
    assert transport.messages() == [_raw("m-1", 1)]
    assert transport.messages() == []  # exhausted
    assert transport.ping() is True
    assert client.pinged == 1
    transport.close()
    assert client.pubsubs[0].closed is True
    # A transport that was never subscribed reports no messages.
    assert RedisPubSubTransport(client=_FakeClient()).messages() == []


def test_redis_transport_ping_is_fail_safe() -> None:
    from apps.realtime.broker import RedisPubSubTransport

    class _BrokenClient:
        def pubsub(self) -> None:
            raise ConnectionError("down")

        def ping(self) -> bool:
            raise ConnectionError("down")

    transport = RedisPubSubTransport(client=_BrokenClient())
    assert transport.ping() is False
