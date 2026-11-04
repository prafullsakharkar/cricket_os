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
from urllib.parse import urlsplit

from django.conf import settings
from django.http import JsonResponse, StreamingHttpResponse
from django.views import View

from apps.core import metrics
from apps.core.errors import error_payload
from apps.core.net import client_ip
from apps.core.throttling import get_connection_limiter
from apps.realtime import wiring
from apps.realtime.frames import Frame
from apps.realtime.hub import Hub, Subscription

logger = logging.getLogger("apps.realtime.sse")

SSE_CONTENT_TYPE = "text/event-stream"
DEFAULT_HEARTBEAT_SECONDS = 15.0

#: Connection-cap keys are transport-namespaced so SSE and WS counts stay
#: independent (the shared limiter is per process = per node).
_KEY_IP = "sse:ip:{}"
_KEY_MATCH = "sse:match:{}"
_KEY_NODE = "sse:node"


def _encode(frame: Frame) -> str:
    """One hub frame as an SSE event block (``id``/``event``/``data``)."""
    data = json.dumps(frame.to_dict(), separators=(",", ":"), default=str)
    return f"id: {frame.seq}\nevent: {frame.event_type}\ndata: {data}\n\n"


def _parse_seq(raw: str | None) -> int | None:
    """Parse a resumption position; ``None`` when absent or malformed."""
    if raw is None or raw == "":
        return None
    try:
        seq = int(raw)
    except TypeError, ValueError:
        return None
    return seq if seq >= 0 else None


class SseMatchFeed(View):
    """``GET /api/v1/sse/matches/{id}/`` — one-way live scoreboard stream."""

    #: Test hook: a fixed heartbeat (``None`` -> ``REALTIME_SSE_HEARTBEAT_SECONDS``).
    heartbeat_seconds: float | None = None

    def _heartbeat(self) -> float:
        if self.heartbeat_seconds is not None:
            return self.heartbeat_seconds
        configured = getattr(settings, "REALTIME_SSE_HEARTBEAT_SECONDS", DEFAULT_HEARTBEAT_SECONDS)
        return float(configured or DEFAULT_HEARTBEAT_SECONDS)

    def _caps(self) -> tuple[int, int, int]:
        """(per-node, per-match, per-IP) connection limits.

        Must stay aligned with the ``keys`` acquisition order in :meth:`get`
        (node -> match -> IP): the hard node bound first, the most specific
        (IP) last.
        """
        return (
            int(getattr(settings, "REALTIME_CONNECTION_CAP_PER_NODE", 10000)),
            int(getattr(settings, "REALTIME_CONNECTION_CAP_PER_MATCH", 2000)),
            int(getattr(settings, "REALTIME_CONNECTION_CAP_PER_IP", 8)),
        )

    def _origin_ok(self, request: Any) -> bool:
        """True when no Origin, same-origin, or an explicit allowlist entry."""
        origin = request.headers.get("Origin")
        if not origin:
            return True  # non-browser client
        # Comma-separated allowlist setting (never a raw string to split).
        raw = str(getattr(settings, "REALTIME_ALLOWED_ORIGINS", "") or "")
        allowed = {item.strip() for item in raw.split(",") if item.strip()}
        try:
            parts = urlsplit(origin)
        except ValueError:
            return False
        if not parts.scheme or not parts.netloc:
            return False
        if parts.scheme == request.scheme and parts.netloc == request.get_host():
            return True  # same-origin request
        return origin in allowed or f"{parts.scheme}://{parts.netloc}" in allowed

    async def get(self, request: Any, pk: Any) -> StreamingHttpResponse | JsonResponse:
        """Open an SSE stream for ``matches/{pk}`` (or a 403/429 rejection)."""
        match_id = str(pk)
        if not self._origin_ok(request):
            return JsonResponse(
                error_payload("origin_not_allowed", "Origin is not allowed for this stream"),
                status=403,
            )

        caps = self._caps()
        limiter = get_connection_limiter()
        keys = (
            _KEY_NODE,
            _KEY_MATCH.format(match_id),
            _KEY_IP.format(client_ip(request)),
        )
        for key, limit in zip(keys, caps, strict=True):
            if not limiter.try_acquire(key, limit=limit):
                for acquired in keys[: keys.index(key)]:
                    limiter.release(acquired)
                metrics.REALTIME_CAP_DENIED.inc()
                return JsonResponse(
                    error_payload("connection_limit", "Connection limit reached for this stream"),
                    status=429,
                )

        from_seq = _parse_seq(request.headers.get("Last-Event-ID"))
        if from_seq is None:
            from_seq = _parse_seq(request.GET.get("from_seq"))
        hub = wiring.get_hub()
        try:
            sub = await hub.subscribe(match_id, last_seq=from_seq)
        except Exception:
            for key in keys:
                limiter.release(key)
            raise

        response = StreamingHttpResponse(self._stream(hub, sub, keys))
        # Exact media type (no charset suffix): some intermediaries match SSE
        # streams on the literal header value.
        response["Content-Type"] = SSE_CONTENT_TYPE
        response["Cache-Control"] = "no-cache, no-transform"
        response["X-Accel-Buffering"] = "no"
        return response

    async def _stream(
        self, hub: Hub, sub: Subscription, keys: tuple[str, ...]
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
            limiter = get_connection_limiter()
            for key in keys:
                limiter.release(key)


__all__ = ["SSE_CONTENT_TYPE", "SseMatchFeed", "_encode", "_parse_seq"]
