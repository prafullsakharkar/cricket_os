"""WebSocket transport (API_SPEC §3.8, ledger Group 8).

The WS consumer is the bidirectional twin of the SSE feed: same hub, same
frames, same Origin + caps policy (``ws:*`` cap keys), same resumption via
``from_seq``. Driven end-to-end through ``config.asgi.application`` with a
hand-rolled WebSocket communicator (``channels.testing`` imports daphne,
which is not installed here).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from typing import Any

import pytest
from django.test import override_settings

from apps.core import metrics
from apps.core.throttling import get_connection_limiter
from apps.realtime import wiring
from apps.realtime.frames import Frame
from apps.realtime.hub import Hub
from apps.realtime.security import (
    CLOSE_BAD_MESSAGE,
    CLOSE_CONNECTION_LIMIT,
    CLOSE_GOING_AWAY,
    CLOSE_MESSAGE_TOO_LARGE,
    CLOSE_ORIGIN_DENIED,
    CLOSE_SERVER_ERROR,
    cap_keys,
)

from .test_realtime_hub import FakeReader

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _reset_wiring() -> Any:
    wiring.reset()
    yield
    wiring.reset()


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _frame(match_id: str, seq: int, event_type: str = "delivery.appended") -> Frame:
    return Frame(
        seq=seq,
        event_type=event_type,
        match_id=match_id,
        payload={"v": 1, "match_id": match_id, "delivery_seq": seq},
        created_at="2026-11-04T10:00:00Z",
    )


class _WebsocketCommunicator:
    """Minimal websocket ASGI driver (channels.testing needs daphne)."""

    def __init__(
        self,
        app: Any,
        path: str,
        *,
        query_string: bytes = b"",
        headers: list[tuple[bytes, bytes]] | None = None,
    ) -> None:
        self._app = app
        self._inputs: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._outputs: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[Any] | None = None
        self._scope = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "scheme": "ws",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query_string,
            "root_path": "",
            "headers": headers or [(b"host", b"testserver")],
            "client": ["127.0.0.1", 12345],
            "server": ["testserver", 80],
            "subprotocols": [],
        }

    async def __aenter__(self) -> _WebsocketCommunicator:
        self._task = asyncio.create_task(
            self._app(self._scope, self._inputs.get, self._outputs.put)
        )
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task

    async def connect(self) -> None:
        await self._inputs.put({"type": "websocket.connect"})

    async def disconnect(self, code: int = 1000) -> None:
        await self._inputs.put({"type": "websocket.disconnect", "code": code})

    async def send_text(self, text: str) -> None:
        await self._inputs.put({"type": "websocket.receive", "text": text})

    async def send_bytes(self, data: bytes) -> None:
        await self._inputs.put({"type": "websocket.receive", "bytes": data})

    async def _receive(self, *, timeout: float = 2.0) -> dict[str, Any]:
        return await asyncio.wait_for(self._outputs.get(), timeout)

    async def receive_message(self, *, timeout: float = 2.0) -> dict[str, Any]:
        return await self._receive(timeout=timeout)

    async def receive_json(self, *, timeout: float = 2.0) -> dict[str, Any]:
        message = await self._receive(timeout=timeout)
        assert message["type"] == "websocket.send"
        return json.loads(message["text"])


async def _expect_accept(comm: _WebsocketCommunicator) -> None:
    message = await comm.receive_message()
    assert message["type"] == "websocket.accept"


async def _expect_close(comm: _WebsocketCommunicator, code: int) -> None:
    message = await comm.receive_message()
    assert message["type"] == "websocket.close"
    assert message.get("code") == code


def _ws_url(match_id: Any) -> str:
    return f"/api/v1/ws/matches/{match_id}/"


# --------------------------------------------------------------------------- #
# Connect + frames
# --------------------------------------------------------------------------- #


def test_connect_accepts_and_streams_live_frames() -> None:
    async def scenario() -> None:
        import config.asgi

        hub = Hub(reader=FakeReader())
        wiring.configure(hub=hub)
        match_id = str(uuid.uuid4())
        async with _WebsocketCommunicator(config.asgi.application, _ws_url(match_id)) as comm:
            await _expect_accept(comm)
            assert hub.subscriber_count() == 1
            await hub.publish(_frame(match_id, 1))
            await hub.tick()
            message = await comm.receive_json()
            assert message["seq"] == 1
            assert message["event_type"] == "delivery.appended"
            assert message["match_id"] == match_id
            await comm.disconnect()
            await asyncio.sleep(0.05)
        assert hub.subscriber_count() == 0

    _run(scenario())


def test_batch_frame_is_delivered_as_one_json_message() -> None:
    async def scenario() -> None:
        import config.asgi

        hub = Hub(reader=FakeReader())
        wiring.configure(hub=hub)
        match_id = str(uuid.uuid4())
        async with _WebsocketCommunicator(config.asgi.application, _ws_url(match_id)) as comm:
            await _expect_accept(comm)
            await hub.publish(_frame(match_id, 1))
            await hub.publish(_frame(match_id, 2))
            await hub.tick()
            message = await comm.receive_json()
            assert message["event_type"] == "batch"
            assert message["seq"] == 2
            assert message["payload"]["count"] == 2
            await comm.disconnect()
            await asyncio.sleep(0.05)

    _run(scenario())


# --------------------------------------------------------------------------- #
# Resumption
# --------------------------------------------------------------------------- #


def test_from_seq_replays_committed_events() -> None:
    async def scenario() -> None:
        import config.asgi

        match_id = str(uuid.uuid4())
        reader = FakeReader()
        reader.log[match_id] = {
            1: {"event_type": "delivery.appended"},
            2: {"event_type": "innings.updated"},
        }
        hub = Hub(reader=reader)
        wiring.configure(hub=hub)
        async with _WebsocketCommunicator(
            config.asgi.application, _ws_url(match_id), query_string=b"from_seq=1"
        ) as comm:
            await _expect_accept(comm)
            message = await comm.receive_json()
            assert message["seq"] == 2
            assert message["event_type"] == "innings.updated"
            await comm.disconnect()
            await asyncio.sleep(0.05)

    _run(scenario())


def test_resync_notice_is_delivered() -> None:
    async def scenario() -> None:
        import config.asgi

        match_id = str(uuid.uuid4())
        reader = FakeReader()
        reader.resync_on.add(match_id)
        hub = Hub(reader=reader)
        wiring.configure(hub=hub)
        async with _WebsocketCommunicator(
            config.asgi.application, _ws_url(match_id), query_string=b"from_seq=50"
        ) as comm:
            await _expect_accept(comm)
            message = await comm.receive_json()
            assert message["event_type"] == "resync_required"
            assert message["payload"]["reason"] == "retention_window"
            await comm.disconnect()
            await asyncio.sleep(0.05)

    _run(scenario())


# --------------------------------------------------------------------------- #
# Origin + caps
# --------------------------------------------------------------------------- #


def test_cross_origin_is_rejected_with_4403() -> None:
    async def scenario() -> None:
        import config.asgi

        hub = Hub(reader=FakeReader())
        wiring.configure(hub=hub)
        match_id = str(uuid.uuid4())
        headers = [(b"host", b"testserver"), (b"origin", b"https://evil.example")]
        async with _WebsocketCommunicator(
            config.asgi.application, _ws_url(match_id), headers=headers
        ) as comm:
            await _expect_close(comm, CLOSE_ORIGIN_DENIED)
        assert hub.subscriber_count() == 0
        assert get_connection_limiter().count("ws:node") == 0

    _run(scenario())


def test_same_origin_and_allowlisted_origins_are_accepted() -> None:
    async def scenario() -> None:
        import config.asgi

        hub = Hub(reader=FakeReader())
        wiring.configure(hub=hub)
        match_id = str(uuid.uuid4())
        # Same-origin: ws://testserver with Origin http://testserver.
        headers = [(b"host", b"testserver"), (b"origin", b"http://testserver")]
        async with _WebsocketCommunicator(
            config.asgi.application, _ws_url(match_id), headers=headers
        ) as comm:
            await _expect_accept(comm)
            await comm.disconnect()
            await asyncio.sleep(0.05)
        with override_settings(REALTIME_ALLOWED_ORIGINS="https://app.example"):
            headers = [(b"host", b"testserver"), (b"origin", b"https://app.example")]
            async with _WebsocketCommunicator(
                config.asgi.application, _ws_url(match_id), headers=headers
            ) as comm:
                await _expect_accept(comm)
                await comm.disconnect()
                await asyncio.sleep(0.05)

    _run(scenario())


def test_per_ip_cap_rejects_with_4429_and_releases_partial_slots() -> None:
    async def scenario() -> None:
        import config.asgi

        hub = Hub(reader=FakeReader())
        wiring.configure(hub=hub)
        match_id = str(uuid.uuid4())
        with override_settings(REALTIME_CONNECTION_CAP_PER_IP=1):
            limiter = get_connection_limiter()
            assert limiter.try_acquire("ws:ip:127.0.0.1", limit=1)
            async with _WebsocketCommunicator(config.asgi.application, _ws_url(match_id)) as comm:
                await _expect_close(comm, CLOSE_CONNECTION_LIMIT)
            # The node/match slots acquired before the IP cap were released.
            assert limiter.count("ws:node") == 0
            assert limiter.count(f"ws:match:{match_id}") == 0
            assert "realtime_cap_denied_total" in metrics.render()
            # Freeing the slot allows a fresh stream.
            limiter.release("ws:ip:127.0.0.1")
            async with _WebsocketCommunicator(config.asgi.application, _ws_url(match_id)) as comm:
                await _expect_accept(comm)
                await comm.disconnect()
                await asyncio.sleep(0.05)
            assert limiter.count("ws:node") == 0

    _run(scenario())


def test_spoofed_forwarded_for_cannot_bypass_the_ip_cap() -> None:
    async def scenario() -> None:
        import config.asgi

        hub = Hub(reader=FakeReader())
        wiring.configure(hub=hub)
        match_id = str(uuid.uuid4())
        with override_settings(REALTIME_CONNECTION_CAP_PER_IP=1):
            # TRUSTED_PROXY_HOPS=0: x-forwarded-for is ignored, so the spoofed
            # header cannot switch this connection to another IP bucket.
            headers = [(b"host", b"testserver"), (b"x-forwarded-for", b"9.9.9.9")]
            first = _WebsocketCommunicator(
                config.asgi.application, _ws_url(match_id), headers=headers
            )
            async with first:
                await _expect_accept(first)
                denied = _WebsocketCommunicator(
                    config.asgi.application, _ws_url(match_id), headers=headers
                )
                async with denied:
                    await _expect_close(denied, CLOSE_CONNECTION_LIMIT)
                await first.disconnect()
                await asyncio.sleep(0.05)

    _run(scenario())


# --------------------------------------------------------------------------- #
# Message validation + ping/pong
# --------------------------------------------------------------------------- #


def test_ping_is_answered_with_pong() -> None:
    async def scenario() -> None:
        import config.asgi

        hub = Hub(reader=FakeReader())
        wiring.configure(hub=hub)
        match_id = str(uuid.uuid4())
        async with _WebsocketCommunicator(config.asgi.application, _ws_url(match_id)) as comm:
            await _expect_accept(comm)
            await comm.send_text(json.dumps({"type": "ping"}))
            message = await comm.receive_json()
            assert message == {"type": "pong"}
            await comm.disconnect()
            await asyncio.sleep(0.05)

    _run(scenario())


def test_binary_ping_message_is_decoded_and_answered() -> None:
    async def scenario() -> None:
        import config.asgi

        hub = Hub(reader=FakeReader())
        wiring.configure(hub=hub)
        match_id = str(uuid.uuid4())
        async with _WebsocketCommunicator(config.asgi.application, _ws_url(match_id)) as comm:
            await _expect_accept(comm)
            await comm.send_bytes(json.dumps({"type": "ping"}).encode())
            message = await comm.receive_json()
            assert message == {"type": "pong"}
            await comm.disconnect()
            await asyncio.sleep(0.05)

    _run(scenario())


def test_unknown_message_type_is_ignored() -> None:
    async def scenario() -> None:
        import config.asgi

        hub = Hub(reader=FakeReader())
        wiring.configure(hub=hub)
        match_id = str(uuid.uuid4())
        async with _WebsocketCommunicator(config.asgi.application, _ws_url(match_id)) as comm:
            await _expect_accept(comm)
            await comm.send_text(json.dumps({"type": "subscribe", "channel": "x"}))
            await comm.send_text(json.dumps({"type": "ping"}))
            # The unknown message was ignored; only the pong is delivered.
            message = await comm.receive_json()
            assert message == {"type": "pong"}
            await comm.disconnect()
            await asyncio.sleep(0.05)

    _run(scenario())


def test_query_with_empty_segments_still_parses_from_seq() -> None:
    async def scenario() -> None:
        import config.asgi

        match_id = str(uuid.uuid4())
        reader = FakeReader()
        reader.log[match_id] = {
            1: {"event_type": "delivery.appended"},
            2: {"event_type": "delivery.appended"},
        }
        hub = Hub(reader=reader)
        wiring.configure(hub=hub)
        async with _WebsocketCommunicator(
            config.asgi.application,
            _ws_url(match_id),
            query_string=b"x=1&&from_seq=1&",
        ) as comm:
            await _expect_accept(comm)
            message = await comm.receive_json()
            assert message["seq"] == 2
            await comm.disconnect()
            await asyncio.sleep(0.05)

    _run(scenario())


def test_oversized_message_closes_4409_and_releases_resources() -> None:
    async def scenario() -> None:
        import config.asgi

        hub = Hub(reader=FakeReader())
        wiring.configure(hub=hub)
        match_id = str(uuid.uuid4())
        async with _WebsocketCommunicator(config.asgi.application, _ws_url(match_id)) as comm:
            await _expect_accept(comm)
            await comm.send_text("x" * 5000)
            await _expect_close(comm, CLOSE_MESSAGE_TOO_LARGE)
            # A later client-side disconnect is a no-op: the close path already
            # tore the pump down and released the caps (no double release).
            await comm.disconnect()
            await asyncio.sleep(0.05)
        assert hub.subscriber_count() == 0
        assert get_connection_limiter().count("ws:node") == 0

    _run(scenario())


def test_pump_send_failure_cleans_up_the_connection() -> None:
    """If ``send`` raises (client gone mid-frame), the pump still releases."""

    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        wiring.configure(hub=hub)
        match_id = str(uuid.uuid4())
        sub = await hub.subscribe(match_id)
        await hub.publish(_frame(match_id, 1))
        await hub.tick()
        assert not sub.queue.empty()

        from apps.realtime.consumers import MatchFeedConsumer

        consumer = MatchFeedConsumer()
        consumer.sub = sub
        consumer._keys = None
        consumer._closed = False
        consumer._pump_task = None

        async def boom_send(**kwargs: Any) -> None:
            raise ConnectionResetError("client gone")

        consumer.send = boom_send  # type: ignore[method-assign]
        await consumer._pump()
        assert consumer._closed is True
        assert hub.subscriber_count() == 0

    _run(scenario())


def test_stop_pump_without_a_task_is_a_noop() -> None:
    async def scenario() -> None:
        from apps.realtime.consumers import MatchFeedConsumer

        consumer = MatchFeedConsumer()
        consumer._pump_task = None
        await consumer._stop_pump()
        assert True

    _run(scenario())


def test_receive_with_no_payload_is_ignored() -> None:
    async def scenario() -> None:
        from apps.realtime.consumers import MatchFeedConsumer

        consumer = MatchFeedConsumer()
        consumer._closed = False
        await consumer.receive()  # both payloads None: no-op
        assert consumer._closed is False

    _run(scenario())


def test_non_json_and_non_object_messages_close_4400() -> None:
    async def scenario() -> None:
        import config.asgi

        hub = Hub(reader=FakeReader())
        wiring.configure(hub=hub)
        match_id = str(uuid.uuid4())
        async with _WebsocketCommunicator(config.asgi.application, _ws_url(match_id)) as comm:
            await _expect_accept(comm)
            await comm.send_text("not-json")
            await _expect_close(comm, CLOSE_BAD_MESSAGE)
            await asyncio.sleep(0.05)
        assert hub.subscriber_count() == 0
        async with _WebsocketCommunicator(config.asgi.application, _ws_url(match_id)) as comm:
            await _expect_accept(comm)
            await comm.send_text("[1, 2, 3]")
            await _expect_close(comm, CLOSE_BAD_MESSAGE)
            await asyncio.sleep(0.05)
        assert hub.subscriber_count() == 0

    _run(scenario())


# --------------------------------------------------------------------------- #
# Drain + failure cleanup
# --------------------------------------------------------------------------- #


def test_drain_notice_is_forwarded_then_closes_1001() -> None:
    async def scenario() -> None:
        import config.asgi

        hub = Hub(reader=FakeReader())
        wiring.configure(hub=hub)
        match_id = str(uuid.uuid4())
        async with _WebsocketCommunicator(config.asgi.application, _ws_url(match_id)) as comm:
            await _expect_accept(comm)
            await hub.drain(grace=0.0, jitter=0.0)
            message = await comm.receive_json()
            assert message["event_type"] == "server.draining"
            await _expect_close(comm, CLOSE_GOING_AWAY)
            await asyncio.sleep(0.05)
        assert hub.subscriber_count() == 0
        assert get_connection_limiter().count("ws:node") == 0

    _run(scenario())


def test_subscribe_failure_closes_1011_and_releases_caps() -> None:
    async def scenario() -> None:
        import config.asgi

        class _BoomHub:
            async def subscribe(self, match_id: str, **kwargs: Any) -> Any:
                raise RuntimeError("hub unavailable")

        wiring.configure(hub=_BoomHub())  # type: ignore[arg-type]
        match_id = str(uuid.uuid4())
        async with _WebsocketCommunicator(config.asgi.application, _ws_url(match_id)) as comm:
            await _expect_close(comm, CLOSE_SERVER_ERROR)
        assert get_connection_limiter().count("ws:node") == 0
        assert get_connection_limiter().count(f"ws:match:{match_id}") == 0

    _run(scenario())


# --------------------------------------------------------------------------- #
# Parity with SSE + routing
# --------------------------------------------------------------------------- #


def test_ws_and_sse_deliver_identical_frame_json() -> None:
    """The same hub frames produce byte-identical JSON on both transports."""

    def sse_data_lines(match_id: str, hub: Hub) -> list[dict[str, Any]]:
        from django.test import RequestFactory

        from apps.realtime.sse import SseMatchFeed

        async def scenario() -> list[dict[str, Any]]:
            request = RequestFactory().get(f"/api/v1/sse/matches/{match_id}/")
            response = await SseMatchFeed.as_view()(request, pk=match_id)
            stream = response.streaming_content
            await hub.publish(_frame(match_id, 1, "delivery.appended"))
            await hub.publish(_frame(match_id, 2, "correction.applied"))
            # Correction flushes immediately: one delivery, then the correction.
            chunk = await stream.__anext__()
            first = json.loads(chunk.split(b"data: ")[1].split(b"\n")[0])
            chunk = await stream.__anext__()
            second = json.loads(chunk.split(b"data: ")[1].split(b"\n")[0])
            await response._iterator.aclose()
            return [first, second]

        return asyncio.run(scenario())

    def ws_messages(match_id: str, hub: Hub) -> list[dict[str, Any]]:
        import config.asgi

        async def scenario() -> list[dict[str, Any]]:
            async with _WebsocketCommunicator(config.asgi.application, _ws_url(match_id)) as comm:
                await _expect_accept(comm)
                await hub.publish(_frame(match_id, 1, "delivery.appended"))
                await hub.publish(_frame(match_id, 2, "correction.applied"))
                first = await comm.receive_json()
                second = await comm.receive_json()
                await comm.disconnect()
                await asyncio.sleep(0.05)
                return [first, second]

        return asyncio.run(scenario())

    hub = Hub(reader=FakeReader())
    wiring.configure(hub=hub)
    match_id = str(uuid.uuid4())
    assert sse_data_lines(match_id, hub) == ws_messages(match_id, hub)


def test_websocket_route_rejects_non_uuid_paths() -> None:
    import re

    from apps.realtime.routing import websocket_urlpatterns
    from apps.realtime.security import UUID_RE

    pattern = websocket_urlpatterns[0].pattern.regex.pattern
    good = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
    # Channels' URLRouter strips the leading slash before matching, exactly
    # like Django's HTTP URL resolution.
    assert re.match(pattern, f"api/v1/ws/matches/{good}/")
    assert re.match(pattern, "api/v1/ws/matches/not-a-uuid/") is None
    assert re.fullmatch(UUID_RE, good)


def test_ws_cap_keys_are_transport_namespaced() -> None:
    keys = cap_keys("ws", "m-1", "1.2.3.4")
    assert keys == ("ws:node", "ws:match:m-1", "ws:ip:1.2.3.4")
    sse_keys = cap_keys("sse", "m-1", "1.2.3.4")
    assert keys[0] != sse_keys[0]
