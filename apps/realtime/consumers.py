"""WebSocket transport (API_SPEC §3.8, ledger Group 8).

Bidirectional twin of the SSE feed: same hub, same frames, same policy. A
Channels consumer subscribes to the shared hub (``apps.realtime.wiring``,
installed by the composition root) and forwards every frame as a JSON text
message whose shape is identical to the SSE ``data:`` line, so a client can
swap transports without touching its frame parsing (parity contract in
``docs/FRONTEND_CONTRACT.md``).

* **Resumption** — ``?from_seq=N`` on connect triggers the hub's log replay,
  exactly like SSE's ``Last-Event-ID``/``from_seq``.
* **Origin + caps** — the same ``apps.realtime.security`` checks as SSE, with
  ``ws:*``-namespaced cap keys, before ``accept()`` so a denied client never
  consumes hub or limiter state. Denials use application close codes (4403
  origin, 4429 connection limit).
* **Message limits** — client frames over ``MAX_CLIENT_MESSAGE_BYTES`` close
  ``4409``; non-JSON or non-object frames close ``4400``; ``{"type":"ping"}``
  is answered with ``{"type":"pong"}``; everything else is ignored (the feed
  is one-way; recovery is reconnect-with-``from_seq``).
* **Drain** — the hub's ``server.draining`` notice is forwarded, then the
  socket closes ``1001`` so clients reconnect elsewhere before the node stops.
* **Cleanup** — ``disconnect`` (and the pump's ``finally``, idempotently)
  unsubscribe and release the caps.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, cast

from channels.generic.websocket import AsyncWebsocketConsumer

from apps.core.throttling import get_connection_limiter
from apps.realtime import wiring
from apps.realtime.frames import Frame
from apps.realtime.hub import Subscription
from apps.realtime.security import (
    CLOSE_BAD_MESSAGE,
    CLOSE_CONNECTION_LIMIT,
    CLOSE_GOING_AWAY,
    CLOSE_MESSAGE_TOO_LARGE,
    CLOSE_ORIGIN_DENIED,
    CLOSE_SERVER_ERROR,
    acquire_caps,
    cap_keys,
    origin_allowed,
    parse_seq,
    release_caps,
    ws_client_ip,
)

logger = logging.getLogger("apps.realtime.ws")

#: Client->server frames larger than this are rejected (a one-way scoreboard
#: needs at most a tiny ping/resume message).
MAX_CLIENT_MESSAGE_BYTES = 4096

#: The drain notice after which the consumer closes (1001 = going away).
_DRAIN_EVENT_TYPE = "server.draining"


class MatchFeedConsumer(AsyncWebsocketConsumer):  # type: ignore[misc]
    """``GET /api/v1/ws/matches/{id}/`` — one-way live scoreboard stream."""

    def _header(self, name: bytes) -> str | None:
        for key, value in self.scope.get("headers") or ():
            header_name = cast(bytes, key)
            header_value = cast(bytes, value)
            if header_name.lower() == name:
                return header_value.decode("latin-1")
        return None

    def _query(self, name: str) -> str | None:
        raw = self.scope.get("query_string") or b""
        for pair in raw.decode("latin-1").split("&"):
            if not pair:
                continue
            key, _, value = pair.partition("=")
            if key == name:
                return value
        return None

    # -- lifecycle ------------------------------------------------------------ #

    async def connect(self) -> None:
        self.match_id = str(self.scope["url_route"]["kwargs"]["pk"])
        self._closed = False
        self._pump_task: asyncio.Task[Any] | None = None
        self._keys: tuple[str, str, str] | None = None

        host = self._header(b"host") or "localhost"
        origin = self._header(b"origin")
        if not origin_allowed(origin, scheme=self.scope.get("scheme", "ws"), host=host):
            await self.close(code=CLOSE_ORIGIN_DENIED)
            return

        self._keys = cap_keys("ws", self.match_id, ws_client_ip(self.scope))
        if not acquire_caps(get_connection_limiter(), self._keys):
            await self.close(code=CLOSE_CONNECTION_LIMIT)
            return

        from_seq = parse_seq(self._query("from_seq"))
        try:
            self.sub: Subscription = await wiring.get_hub().subscribe(
                self.match_id, last_seq=from_seq
            )
        except Exception:
            self._release_caps()
            await self.close(code=CLOSE_SERVER_ERROR)
            return

        await self.accept()
        self._pump_task = asyncio.create_task(self._pump())

    async def disconnect(self, code: Any) -> None:
        """Client (or server) closed: stop the pump and release everything."""
        await self._stop_pump()
        if not self._closed:
            self._cleanup()

    async def _stop_pump(self) -> None:
        if self._pump_task is not None:
            self._pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._pump_task

    async def _shutdown_and_close(self, code: int) -> None:
        """Close a mid-connection connection and release its resources.

        When the app itself closes (message-limit violation, drain), Channels
        does not synthesize a ``websocket.disconnect`` event, so the pump must
        be stopped and the caps released here, or the slot leaks.
        """
        await self._stop_pump()
        if not self._closed:
            self._cleanup()
        await self.close(code=code)

    # -- client messages ------------------------------------------------------ #

    async def receive(self, text_data: str | None = None, bytes_data: bytes | None = None) -> None:
        raw = text_data
        if raw is None and bytes_data is not None:
            raw = bytes_data.decode("utf-8", "replace")
        if raw is None:
            return
        if len(raw.encode("utf-8")) > MAX_CLIENT_MESSAGE_BYTES:
            await self._shutdown_and_close(CLOSE_MESSAGE_TOO_LARGE)
            return
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            await self._shutdown_and_close(CLOSE_BAD_MESSAGE)
            return
        if not isinstance(message, dict):
            await self._shutdown_and_close(CLOSE_BAD_MESSAGE)
            return
        if message.get("type") == "ping":
            await self.send(text_data=json.dumps({"type": "pong"}))
        # Any other message type is ignored (documented in FRONTEND_CONTRACT).

    # -- delivery ------------------------------------------------------------- #

    async def _pump(self) -> None:
        """Forward hub frames as JSON text messages until drain or disconnect."""
        try:
            while not self._closed:
                frame: Frame = await self.sub.queue.get()
                await self.send(text_data=frame.to_json())
                if frame.event_type == _DRAIN_EVENT_TYPE:
                    await self.close(code=CLOSE_GOING_AWAY)
                    return
        except Exception:
            pass  # socket gone; cleanup happens in disconnect()
        finally:
            if not self._closed:
                self._cleanup()

    def _cleanup(self) -> None:
        if self._closed:
            return
        self._closed = True
        wiring.get_hub().unsubscribe(self.sub)
        self._release_caps()

    def _release_caps(self) -> None:
        if self._keys is not None:
            release_caps(get_connection_limiter(), self._keys)
            self._keys = None


__all__ = ["CLOSE_GOING_AWAY", "MAX_CLIENT_MESSAGE_BYTES", "MatchFeedConsumer"]
