"""Transport-shared Origin checks, connection caps, and resume parsing.

SSE (Group 7) and WebSocket (Group 8) enforce identical policy before a
subscription is created, so a denied client consumes no hub or limiter state
and a browser cannot open a cross-site scoreboard stream through either
transport:

* **Origin** — no ``Origin`` (non-browser client) is allowed; a same-origin
  request (``ws``/``wss`` mapped to ``http``/``https`` for comparison) or an
  explicit ``REALTIME_ALLOWED_ORIGINS`` allowlist entry passes; anything else
  is rejected.
* **Connection caps** — per-node, per-match and per-IP limits drawn from a
  single process-wide :class:`apps.core.throttling.ConnectionLimiter`. Keys
  are transport-namespaced (``sse:*`` / ``ws:*``) so the two transports draw
  against the same per-node budget but keep independent per-transport counts.
  Caps are acquired node -> match -> IP and released in ``finally``/``disconnect``.
* **Resume position** — a single ``parse_seq`` helper gives SSE
  (``Last-Event-ID``/``from_seq``) and WS (``from_seq``) the same malformed-input
  handling.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings

from apps.core import metrics
from apps.core.net import client_ip
from apps.core.throttling import ConnectionLimiter

DEFAULT_CAP_PER_IP = 8
DEFAULT_CAP_PER_MATCH = 2000
DEFAULT_CAP_PER_NODE = 10000

#: Application close codes for the WS transport (documented in
#: ``docs/FRONTEND_CONTRACT.md``). 4xxx is the RFC 6455 application range.
CLOSE_ORIGIN_DENIED = 4403
CLOSE_CONNECTION_LIMIT = 4429
CLOSE_BAD_MESSAGE = 4400
CLOSE_MESSAGE_TOO_LARGE = 4409
CLOSE_SERVER_ERROR = 1011
CLOSE_GOING_AWAY = 1001

#: Matches Django's UUID path converter so the WS route rejects non-UUID ids
#: exactly like the SSE HTTP route.
UUID_RE = (
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def parse_seq(raw: str | None) -> int | None:
    """Parse a resumption position; ``None`` when absent or malformed."""
    if raw is None or raw == "":
        return None
    try:
        seq = int(raw)
    except TypeError, ValueError:
        return None
    return seq if seq >= 0 else None


def origin_allowed(origin: str | None, *, scheme: str, host: str) -> bool:
    """True when no Origin, same-origin, or an explicit allowlist entry.

    ``scheme``/``host`` are the request's own (``ws``/``wss`` map to
    ``http``/``https`` so a browser's ``Origin`` compares correctly).
    """
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
    http_scheme = {"ws": "http", "wss": "https"}.get(scheme, scheme)
    if parts.scheme == http_scheme and parts.netloc == host:
        return True  # same-origin request
    return origin in allowed or f"{parts.scheme}://{parts.netloc}" in allowed


def cap_limits() -> tuple[int, int, int]:
    """(per-node, per-match, per-IP) connection limits from settings.

    Order must stay aligned with the ``cap_keys`` acquisition order
    (node -> match -> IP): the hard node bound first, the most specific
    (IP) last.
    """
    return (
        int(getattr(settings, "REALTIME_CONNECTION_CAP_PER_NODE", DEFAULT_CAP_PER_NODE)),
        int(getattr(settings, "REALTIME_CONNECTION_CAP_PER_MATCH", DEFAULT_CAP_PER_MATCH)),
        int(getattr(settings, "REALTIME_CONNECTION_CAP_PER_IP", DEFAULT_CAP_PER_IP)),
    )


def cap_keys(transport: str, match_id: str, ip: str) -> tuple[str, str, str]:
    """(node, match, ip) limiter keys, namespaced per transport."""
    return (
        f"{transport}:node",
        f"{transport}:match:{match_id}",
        f"{transport}:ip:{ip}",
    )


def acquire_caps(limiter: ConnectionLimiter, keys: tuple[str, str, str]) -> bool:
    """Acquire node -> match -> ip caps; ``False`` (and released) on denial.

    A denied client releases every already-acquired slot and counts
    ``REALTIME_CAP_DENIED``.
    """
    limits = cap_limits()
    for key, limit in zip(keys, limits, strict=True):
        if not limiter.try_acquire(key, limit=limit):
            for acquired in keys[: keys.index(key)]:
                limiter.release(acquired)
            metrics.REALTIME_CAP_DENIED.inc()
            return False
    return True


def release_caps(limiter: ConnectionLimiter, keys: tuple[str, str, str]) -> None:
    """Release every cap held by a connection (idempotent per key)."""
    for key in keys:
        limiter.release(key)


def ws_client_ip(scope: Any) -> str:
    """The client IP from a websocket scope, using the shared trusted-proxy logic.

    Builds the same ``META`` shape :func:`apps.core.net.client_ip` expects from
    the ASGI scope (``client`` + ``x-forwarded-for`` header) so WS scoping is
    identical to SSE's (``TRUSTED_PROXY_HOPS=0`` ignores the header).
    """
    headers = {k.lower(): v for k, v in (scope.get("headers") or ())}
    remote = scope.get("client") or ["unknown", 0]
    meta: dict[str, str] = {"REMOTE_ADDR": str(remote[0])}
    forwarded = headers.get(b"x-forwarded-for")
    if forwarded:
        meta["HTTP_X_FORWARDED_FOR"] = forwarded.decode("latin-1")
    return client_ip(SimpleNamespace(META=meta))


__all__ = [
    "CLOSE_BAD_MESSAGE",
    "CLOSE_CONNECTION_LIMIT",
    "CLOSE_GOING_AWAY",
    "CLOSE_MESSAGE_TOO_LARGE",
    "CLOSE_ORIGIN_DENIED",
    "CLOSE_SERVER_ERROR",
    "UUID_RE",
    "acquire_caps",
    "cap_keys",
    "cap_limits",
    "origin_allowed",
    "parse_seq",
    "release_caps",
    "ws_client_ip",
]
