"""Server-Sent Events transport (API_SPEC §3.8, ledger Group 7).

One-way scoreboard feed over plain HTTP — no Channels needed. An async Django
view subscribes to the shared hub (``apps.realtime.wiring``, injected by the
composition root) and streams hub frames as SSE events. Coalescing, gap
backfill, resync and licensing degradation are all hub behaviour; this module
only encodes and delivers.

* **Resumption** — the standard ``Last-Event-ID`` header (or a ``from_seq``
  query parameter) triggers a log replay on subscribe, so a reconnect after a
  dropped stream resumes exactly where the client left off.
* **Heartbeats** — a comment line every ``REALTIME_SSE_HEARTBEAT_SECONDS``
  keeps intermediaries from closing the idle connection.
* **Origin + caps** — a cross-site ``Origin`` is rejected (``403``
  ``origin_not_allowed``); per-IP, per-match and per-node connection caps
  return ``429`` ``connection_limit``. Both are checked before a subscription
  is created, so a denied client consumes no hub or limiter state.
* **Cleanup** — the stream generator's ``finally`` unsubscribes and releases
  the caps, so counts never leak on disconnect.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from django.conf import settings
from django.http import JsonResponse, StreamingHttpResponse
from django.views import View

from apps.core.errors import error_payload
from apps.core.net import client_ip
from apps.core.throttling import get_connection_limiter
from apps.realtime import wiring
from apps.realtime.frames import Frame
from apps.realtime.hub import Hub, Subscription
from apps.realtime.security import (
    acquire_caps,
    cap_keys,
    origin_allowed,
    parse_seq,
    release_caps,
)

logger = logging.getLogger("apps.realtime.sse")

SSE_CONTENT_TYPE = "text/event-stream"
DEFAULT_HEARTBEAT_SECONDS = 15.0


def _encode(frame: Frame) -> str:
    """One hub frame as an SSE event block (``id``/``event``/``data``)."""
    data = json.dumps(frame.to_dict(), separators=(",", ":"), default=str)
    return f"id: {frame.seq}\nevent: {frame.event_type}\ndata: {data}\n\n"


class SseMatchFeed(View):
    """``GET /api/v1/sse/matches/{id}/`` — one-way live scoreboard stream."""

    #: Test hook: a fixed heartbeat (``None`` -> ``REALTIME_SSE_HEARTBEAT_SECONDS``).
    heartbeat_seconds: float | None = None

    def _heartbeat(self) -> float:
        if self.heartbeat_seconds is not None:
            return self.heartbeat_seconds
        configured = getattr(settings, "REALTIME_SSE_HEARTBEAT_SECONDS", DEFAULT_HEARTBEAT_SECONDS)
        return float(configured or DEFAULT_HEARTBEAT_SECONDS)

    async def get(self, request: Any, pk: Any) -> StreamingHttpResponse | JsonResponse:
        """Open an SSE stream for ``matches/{pk}`` (or a 403/429 rejection)."""
        match_id = str(pk)
        origin = request.headers.get("Origin")
        if not origin_allowed(origin, scheme=request.scheme, host=request.get_host()):
            return JsonResponse(
                error_payload("origin_not_allowed", "Origin is not allowed for this stream"),
                status=403,
            )

        limiter = get_connection_limiter()
        keys = cap_keys("sse", match_id, client_ip(request))
        if not acquire_caps(limiter, keys):
            return JsonResponse(
                error_payload("connection_limit", "Connection limit reached for this stream"),
                status=429,
            )

        from_seq = parse_seq(request.headers.get("Last-Event-ID"))
        if from_seq is None:
            from_seq = parse_seq(request.GET.get("from_seq"))
        hub = wiring.get_hub()
        try:
            sub = await hub.subscribe(match_id, last_seq=from_seq)
        except Exception:
            release_caps(limiter, keys)
            raise

        response = StreamingHttpResponse(self._stream(hub, sub, keys))
        # Exact media type (no charset suffix): some intermediaries match SSE
        # streams on the literal header value.
        response["Content-Type"] = SSE_CONTENT_TYPE
        response["Cache-Control"] = "no-cache, no-transform"
        response["X-Accel-Buffering"] = "no"
        return response

    async def _stream(
        self, hub: Hub, sub: Subscription, keys: tuple[str, str, str]
    ) -> AsyncIterator[str]:
        """Yield encoded frames (or heartbeat comments) until the client goes."""
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(sub.queue.get(), timeout=self._heartbeat())
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield _encode(frame)
        finally:
            hub.unsubscribe(sub)
            release_caps(get_connection_limiter(), keys)


__all__ = ["SSE_CONTENT_TYPE", "SseMatchFeed", "_encode"]
