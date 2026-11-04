"""SSE transport (API_SPEC §3.8, ledger Group 7).

The SSE feed is an async Django view over the shared hub: it encodes hub
frames as SSE events, resumes via ``Last-Event-ID``/``from_seq``, heartbeats
on idle, and enforces Origin + per-node connection caps. Driven directly
(async view call) for determinism, plus one end-to-end ASGI test through the
composition root so the real routing and wiring are exercised.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from typing import Any

import pytest
from django.test import RequestFactory, override_settings

from apps.core import metrics
from apps.core.throttling import get_connection_limiter
from apps.realtime import wiring
from apps.realtime.frames import Frame
from apps.realtime.hub import Hub
from apps.realtime.sse import SseMatchFeed, _encode

from .test_realtime_hub import FakeReader

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _reset_wiring() -> Any:
    wiring.reset()
    yield
    wiring.reset()


def _feed_url(match_id: Any) -> str:
    return f"/api/v1/sse/matches/{match_id}/"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _close_stream(response: Any) -> None:
    """Close the SSE generator under Django's bytes-encoding wrapper.

    ``StreamingHttpResponse.streaming_content`` wraps an async generator in a
    second generator that yields encoded bytes; ``aclose()`` on that wrapper
    does not propagate to the inner generator, so the view's cleanup ``finally``
    only runs when the underlying ``_iterator`` is closed directly.
    """
    await response._iterator.aclose()


def _frame(match_id: str, seq: int, event_type: str = "delivery.appended") -> Frame:
    return Frame(
        seq=seq,
        event_type=event_type,
        match_id=match_id,
        payload={"v": 1, "match_id": match_id, "delivery_seq": seq},
        created_at="2026-11-04T10:00:00Z",
    )


# --------------------------------------------------------------------------- #
# Encoding + headers
# --------------------------------------------------------------------------- #


def test_encode_produces_an_sse_event_block() -> None:
    block = _encode(_frame("m-1", 7, "delivery.appended"))
    lines = block.split("\n")
    assert lines[0] == "id: 7"
    assert lines[1] == "event: delivery.appended"
    assert lines[2].startswith("data: ")
    assert block.endswith("\n\n")
    payload = json.loads(lines[2][len("data: ") :])
    assert payload["seq"] == 7
    assert payload["match_id"] == "m-1"
    # The frame payload nests under ``payload`` (the full client-frame shape).
    assert payload["payload"]["delivery_seq"] == 7


def test_sse_response_headers() -> None:
    async def scenario() -> None:
        wiring.configure(hub=Hub(reader=FakeReader()))
        request = RequestFactory().get(_feed_url(uuid.uuid4()))
        response = await SseMatchFeed.as_view()(request, pk=str(uuid.uuid4()))
        assert response.status_code == 200
        assert response["Content-Type"] == "text/event-stream"
        assert "no-cache" in response["Cache-Control"]
        assert response["X-Accel-Buffering"] == "no"
        await _close_stream(response)

    _run(scenario())


# --------------------------------------------------------------------------- #
# Live streaming
# --------------------------------------------------------------------------- #


def test_live_frames_stream_as_sse_events() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        wiring.configure(hub=hub)
        match_id = str(uuid.uuid4())
        request = RequestFactory().get(_feed_url(match_id))
        response = await SseMatchFeed.as_view()(request, pk=match_id)
        stream = response.streaming_content
        assert hub.subscriber_count() == 1
        await hub.publish(_frame(match_id, 1))
        await hub.publish(_frame(match_id, 2))
        await hub.tick()
        chunk = await stream.__anext__()
        assert chunk.startswith(b"id: 2\n")
        assert b"event: batch" in chunk
        await _close_stream(response)
        assert hub.subscriber_count() == 0

    _run(scenario())


def test_correction_frame_streams_immediately() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        wiring.configure(hub=hub)
        match_id = str(uuid.uuid4())
        request = RequestFactory().get(_feed_url(match_id))
        response = await SseMatchFeed.as_view()(request, pk=match_id)
        stream = response.streaming_content
        await hub.publish(_frame(match_id, 1, "delivery.appended"))
        await hub.publish(_frame(match_id, 2, "correction.applied"))
        # No tick: the correction flushed the pending delivery immediately.
        chunk = await stream.__anext__()
        assert b"event: delivery.appended" in chunk
        chunk = await stream.__anext__()
        assert b"event: correction.applied" in chunk
        await _close_stream(response)

    _run(scenario())


# --------------------------------------------------------------------------- #
# Resumption
# --------------------------------------------------------------------------- #


def test_last_event_id_replays_committed_events() -> None:
    async def scenario() -> None:
        reader = FakeReader()
        reader.log["m-1"] = {
            1: {"event_type": "delivery.appended"},
            2: {"event_type": "innings.updated"},
        }
        hub = Hub(reader=reader)
        wiring.configure(hub=hub)
        request = RequestFactory().get(_feed_url("m-1"), HTTP_LAST_EVENT_ID="1")
        response = await SseMatchFeed.as_view()(request, pk="m-1")
        chunk = await response.streaming_content.__anext__()
        assert b"id: 2" in chunk
        assert b"event: innings.updated" in chunk
        await _close_stream(response)

    _run(scenario())


def test_from_seq_query_parameter_resumes() -> None:
    async def scenario() -> None:
        reader = FakeReader()
        reader.log["m-1"] = {
            1: {"event_type": "delivery.appended"},
            2: {"event_type": "delivery.appended"},
            3: {"event_type": "match.completed"},
        }
        hub = Hub(reader=reader)
        wiring.configure(hub=hub)
        request = RequestFactory().get(_feed_url("m-1") + "?from_seq=2")
        response = await SseMatchFeed.as_view()(request, pk="m-1")
        chunk = await response.streaming_content.__anext__()
        assert b"id: 3" in chunk
        assert b"event: match.completed" in chunk
        await _close_stream(response)

    _run(scenario())


def test_resync_notice_is_encoded() -> None:
    async def scenario() -> None:
        reader = FakeReader()
        reader.resync_on.add("m-1")
        hub = Hub(reader=reader)
        wiring.configure(hub=hub)
        request = RequestFactory().get(_feed_url("m-1"), HTTP_LAST_EVENT_ID="50")
        response = await SseMatchFeed.as_view()(request, pk="m-1")
        chunk = await response.streaming_content.__anext__()
        assert b"event: resync_required" in chunk
        assert b"retention_window" in chunk
        await _close_stream(response)

    _run(scenario())


# --------------------------------------------------------------------------- #
# Heartbeat
# --------------------------------------------------------------------------- #


def test_heartbeat_is_emitted_when_idle() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        wiring.configure(hub=hub)
        request = RequestFactory().get(_feed_url(uuid.uuid4()))
        with override_settings(REALTIME_SSE_HEARTBEAT_SECONDS=0.02):
            response = await SseMatchFeed.as_view()(request, pk=str(uuid.uuid4()))
            stream = response.streaming_content
            chunk = await stream.__anext__()  # blocks ~0.02s, then the ping
        assert chunk == b": ping\n\n"
        await _close_stream(response)

    _run(scenario())


# --------------------------------------------------------------------------- #
# Origin + caps
# --------------------------------------------------------------------------- #


def test_cross_origin_stream_is_rejected() -> None:
    async def scenario() -> None:
        wiring.configure(hub=Hub(reader=FakeReader()))
        request = RequestFactory().get(_feed_url(uuid.uuid4()), HTTP_ORIGIN="https://evil.example")
        response = await SseMatchFeed.as_view()(request, pk=str(uuid.uuid4()))
        assert response.status_code == 403
        assert json.loads(response.content)["error"]["code"] == "origin_not_allowed"
        assert get_connection_limiter().count("sse:node") == 0

    _run(scenario())


def test_same_origin_and_allowlisted_streams_are_allowed() -> None:
    async def scenario() -> None:
        wiring.configure(hub=Hub(reader=FakeReader()))
        same = RequestFactory().get(_feed_url(uuid.uuid4()), HTTP_ORIGIN="http://testserver")
        response = await SseMatchFeed.as_view()(same, pk=str(uuid.uuid4()))
        assert response.status_code == 200
        await _close_stream(response)
        with override_settings(REALTIME_ALLOWED_ORIGINS="https://app.example"):
            listed = RequestFactory().get(
                _feed_url(uuid.uuid4()), HTTP_ORIGIN="https://app.example"
            )
            response = await SseMatchFeed.as_view()(listed, pk=str(uuid.uuid4()))
            assert response.status_code == 200
            await _close_stream(response)

    _run(scenario())


def test_per_ip_connection_cap_rejects_and_counts() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        wiring.configure(hub=hub)
        match_id = str(uuid.uuid4())
        with override_settings(REALTIME_CONNECTION_CAP_PER_IP=1):
            limiter = get_connection_limiter()
            assert limiter.try_acquire("sse:ip:127.0.0.1", limit=1)
            request = RequestFactory().get(_feed_url(match_id))
            response = await SseMatchFeed.as_view()(request, pk=match_id)
            assert response.status_code == 429
            assert json.loads(response.content)["error"]["code"] == "connection_limit"
            assert "realtime_cap_denied_total" in metrics.render()
            # The node/match slots acquired before the IP cap were released.
            assert limiter.count("sse:node") == 0
            assert limiter.count(f"sse:match:{match_id}") == 0
            # Freeing the slot allows a fresh stream.
            limiter.release("sse:ip:127.0.0.1")
            ok = await SseMatchFeed.as_view()(request, pk=match_id)
            assert ok.status_code == 200
            await _close_stream(ok)

    _run(scenario())


def test_spoofed_forwarded_for_cannot_bypass_the_ip_cap() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        wiring.configure(hub=hub)
        match_id = str(uuid.uuid4())
        with override_settings(REALTIME_CONNECTION_CAP_PER_IP=1):
            # TRUSTED_PROXY_HOPS=0 in tests: X-Forwarded-For is ignored, so the
            # spoofed header cannot switch this request to another IP bucket.
            request = RequestFactory().get(_feed_url(match_id), HTTP_X_FORWARDED_FOR="9.9.9.9")
            first = await SseMatchFeed.as_view()(request, pk=match_id)
            assert first.status_code == 200
            denied = await SseMatchFeed.as_view()(request, pk=match_id)
            assert denied.status_code == 429
            await _close_stream(first)

    _run(scenario())


def test_parse_seq_ignores_malformed_and_negative_positions() -> None:
    from apps.realtime.security import parse_seq

    assert parse_seq(None) is None
    assert parse_seq("") is None
    assert parse_seq("abc") is None
    assert parse_seq("-1") is None
    assert parse_seq("12") == 12


def test_heartbeat_class_override_wins_over_settings() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        wiring.configure(hub=hub)
        request = RequestFactory().get(_feed_url(uuid.uuid4()))
        with override_settings(REALTIME_SSE_HEARTBEAT_SECONDS=60):
            view = SseMatchFeed()
            view.heartbeat_seconds = 0.02  # test hook overrides the setting
            response = await view.get(request, pk=str(uuid.uuid4()))
            stream = response.streaming_content
            assert await stream.__anext__() == b": ping\n\n"
            # The loop continues after a heartbeat: a second idle read also
            # yields a ping (proves the generator is not exhausted).
            assert await stream.__anext__() == b": ping\n\n"
            await _close_stream(response)

    _run(scenario())


def test_malformed_origin_is_rejected() -> None:
    async def scenario() -> None:
        wiring.configure(hub=Hub(reader=FakeReader()))
        # No scheme/netloc and an unparseable Origin both fail closed
        # (``urlsplit`` raises on a truncated IPv6 literal).
        for origin in ("not-a-url", "http://", "http://[::1"):
            request = RequestFactory().get(_feed_url(uuid.uuid4()), HTTP_ORIGIN=origin)
            response = await SseMatchFeed.as_view()(request, pk=str(uuid.uuid4()))
            assert response.status_code == 403
            assert json.loads(response.content)["error"]["code"] == "origin_not_allowed"
        assert get_connection_limiter().count("sse:node") == 0

    _run(scenario())


def test_subscribe_failure_releases_acquired_caps() -> None:
    async def scenario() -> None:
        class _BoomHub:
            async def subscribe(self, match_id: str, **kwargs: Any) -> Any:
                raise RuntimeError("hub unavailable")

        wiring.configure(hub=_BoomHub())  # type: ignore[arg-type]
        request = RequestFactory().get(_feed_url(uuid.uuid4()))
        with pytest.raises(RuntimeError, match="hub unavailable"):
            await SseMatchFeed.as_view()(request, pk=str(uuid.uuid4()))
        limiter = get_connection_limiter()
        assert limiter.count("sse:node") == 0
        assert limiter.count("sse:ip:127.0.0.1") == 0

    _run(scenario())


# --------------------------------------------------------------------------- #
# Cleanup
# --------------------------------------------------------------------------- #


def test_disconnect_releases_subscription_and_caps() -> None:
    async def scenario() -> None:
        hub = Hub(reader=FakeReader())
        wiring.configure(hub=hub)
        match_id = str(uuid.uuid4())
        request = RequestFactory().get(_feed_url(match_id))
        response = await SseMatchFeed.as_view()(request, pk=match_id)
        assert hub.subscriber_count() == 1
        limiter = get_connection_limiter()
        assert limiter.count("sse:node") == 1
        assert limiter.count(f"sse:match:{match_id}") == 1
        assert limiter.count("sse:ip:127.0.0.1") == 1
        # Start the generator (a lazy async generator's body — and its cleanup
        # ``finally`` — only runs once the first chunk is pulled; the ASGI
        # handler does this immediately after sending the start message).
        await hub.publish(_frame(match_id, 1))
        await hub.tick()
        assert await response.streaming_content.__anext__()
        await _close_stream(response)
        assert hub.subscriber_count() == 0
        assert limiter.count("sse:node") == 0
        assert limiter.count(f"sse:match:{match_id}") == 0
        assert limiter.count("sse:ip:127.0.0.1") == 0

    _run(scenario())


# --------------------------------------------------------------------------- #
# End-to-end ASGI
# --------------------------------------------------------------------------- #


class _AsgiCommunicator:
    """Minimal ASGI application driver (channels.testing needs daphne)."""

    def __init__(self, app: Any, scope: dict[str, Any]) -> None:
        self._app = app
        self._scope = scope
        self._inputs: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._outputs: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[Any] | None = None

    async def __aenter__(self) -> _AsgiCommunicator:
        self._task = asyncio.create_task(
            self._app(self._scope, self._inputs.get, self._outputs.put)
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def send_input(self, message: dict[str, Any]) -> None:
        await self._inputs.put(message)

    async def receive_output(self, *, timeout: float = 2.0) -> dict[str, Any]:
        return await asyncio.wait_for(self._outputs.get(), timeout)


def test_asgi_composition_root_serves_the_sse_stream() -> None:
    async def scenario() -> None:
        import config.asgi

        hub = Hub(reader=FakeReader())
        wiring.configure(hub=hub, reader=FakeReader())
        match_id = str(uuid.uuid4())
        path = f"/api/v1/sse/matches/{match_id}/"
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"testserver")],
            "client": ["127.0.0.1", 12345],
            "server": ["testserver", 80],
        }
        async with _AsgiCommunicator(config.asgi.application, scope) as comm:
            await comm.send_input({"type": "http.request", "body": b""})
            start = await comm.receive_output()
            assert start["type"] == "http.response.start"
            assert start["status"] == 200
            headers = {k.lower(): v for k, v in start["headers"]}
            assert headers[b"content-type"].startswith(b"text/event-stream")
            # Publish a frame; read the first SSE chunk over the real ASGI path.
            await hub.publish(_frame(match_id, 1))
            await hub.tick()
            body = await comm.receive_output()
            assert body["type"] == "http.response.body"
            assert b"id: 1" in body["body"]
            assert hub.subscriber_count() == 1
            # Disconnect -> subscription and caps are released.
            await comm.send_input({"type": "http.disconnect"})
            await asyncio.sleep(0.2)
            assert hub.subscriber_count() == 0

    _run(scenario())
