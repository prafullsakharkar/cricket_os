"""Role-separated Redis clients (PERFORMANCE §5/§8, Phase E task 1).

Redis is used for four logically different jobs with different eviction and
durability needs:

===========  ==========================================  =====================
Role         Used for                                    Recommended policy
===========  ==========================================  =====================
``cache``    response bodies, versions, cache locks      ``allkeys-lru``
``pubsub``   ``match:{id}`` realtime fan-out             ``noeviction``
``ratelimit`` token buckets                             ``noeviction``
``broker``   Celery broker                               ``noeviction``
``result``   Celery result backend                       ``noeviction``
===========  ==========================================  =====================

The factory is deliberately the *only* place URLs/timeouts are resolved. Every
call is guarded by :func:`safe_call` / :func:`is_healthy`: a Redis outage must
degrade a feature, never raise into request handling. Locally (and in CI) all
roles may share one server via separate databases; production points each URL
at its own instance.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import redis
from django.conf import settings

logger = logging.getLogger("apps.core.redis")

ROLE_CACHE = "cache"
ROLE_PUBSUB = "pubsub"
ROLE_RATELIMIT = "ratelimit"
ROLE_BROKER = "broker"
ROLE_RESULT = "result"

ROLES: tuple[str, ...] = (
    ROLE_CACHE,
    ROLE_PUBSUB,
    ROLE_RATELIMIT,
    ROLE_BROKER,
    ROLE_RESULT,
)

#: Recommended server policy per role (documented, not enforced client-side).
ROLE_POLICIES: dict[str, str] = {
    ROLE_CACHE: "allkeys-lru",
    ROLE_PUBSUB: "noeviction",
    ROLE_RATELIMIT: "noeviction",
    ROLE_BROKER: "noeviction",
    ROLE_RESULT: "noeviction",
}

DEFAULT_SOCKET_TIMEOUT = 0.25
DEFAULT_CONNECT_TIMEOUT = 0.5
DEFAULT_HEALTH_CHECK_INTERVAL = 30
DEFAULT_MAX_CONNECTIONS = 100

_lock = threading.Lock()
_clients: dict[str, redis.Redis] = {}


def url_with_db(url: str, db: int) -> str:
    """Return ``url`` pointing at logical database ``db`` (for defaults)."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{db}", parts.query, parts.fragment))


def role_url(role: str) -> str:
    """Resolve the configured URL for ``role``."""
    _validate(role)
    fallback = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
    configured = {
        ROLE_CACHE: getattr(settings, "CACHE_REDIS_URL", fallback),
        ROLE_PUBSUB: getattr(settings, "PUBSUB_REDIS_URL", fallback),
        ROLE_RATELIMIT: getattr(settings, "RATELIMIT_REDIS_URL", fallback),
        ROLE_BROKER: getattr(settings, "CELERY_BROKER_URL", fallback),
        ROLE_RESULT: getattr(settings, "CELERY_RESULT_BACKEND", fallback),
    }[role]
    return str(configured or fallback)


def _validate(role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"unknown Redis role {role!r}; expected one of {ROLES}")


def _build_client(url: str, **kwargs: Any) -> redis.Redis:
    """Build a pooled client (kept separate so tests can capture the options)."""
    return redis.Redis.from_url(url, **kwargs)


def client(role: str) -> redis.Redis:
    """A memoised, pooled, timeout-bounded client for ``role``.

    Clients are process-global: creating one per request would exhaust file
    descriptors. ``decode_responses=True`` keeps call sites JSON-friendly.
    """
    _validate(role)
    with _lock:
        existing = _clients.get(role)
        if existing is not None:
            return existing
        built = _build_client(
            role_url(role),
            socket_timeout=getattr(
                settings, "REDIS_SOCKET_TIMEOUT_SECONDS", DEFAULT_SOCKET_TIMEOUT
            ),
            socket_connect_timeout=getattr(
                settings, "REDIS_CONNECT_TIMEOUT_SECONDS", DEFAULT_CONNECT_TIMEOUT
            ),
            health_check_interval=getattr(
                settings, "REDIS_HEALTH_CHECK_INTERVAL_SECONDS", DEFAULT_HEALTH_CHECK_INTERVAL
            ),
            max_connections=getattr(settings, "REDIS_MAX_CONNECTIONS", DEFAULT_MAX_CONNECTIONS),
            decode_responses=True,
        )
        _clients[role] = built
        return built


def reset_clients() -> None:
    """Drop memoised clients (test isolation / fork-safe re-init)."""
    with _lock:
        for connection in _clients.values():
            close = getattr(connection, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):  # pragma: no cover - teardown
                    close()
        _clients.clear()


def is_healthy(role: str) -> bool:
    """``True`` when ``role`` answers a ``PING``; never raises."""
    try:
        client(role).ping()
    except Exception:
        logger.warning("redis role unhealthy", extra={"redis_role": role})
        return False
    return True


def safe_call[T](role: str, operation: Callable[[], T], *, default: T | None = None) -> T | None:
    """Run ``operation`` and return ``default`` on any Redis error.

    Use this for best-effort accelerators (cache, publication, throttling).
    Callers that genuinely need Redis semantics should call :func:`client`
    directly and handle the error themselves.
    """
    try:
        return operation()
    except Exception:
        logger.warning("redis operation failed", extra={"redis_role": role}, exc_info=True)
        return default


__all__ = [
    "DEFAULT_CONNECT_TIMEOUT",
    "DEFAULT_HEALTH_CHECK_INTERVAL",
    "DEFAULT_MAX_CONNECTIONS",
    "DEFAULT_SOCKET_TIMEOUT",
    "ROLES",
    "ROLE_BROKER",
    "ROLE_CACHE",
    "ROLE_POLICIES",
    "ROLE_PUBSUB",
    "ROLE_RATELIMIT",
    "ROLE_RESULT",
    "client",
    "is_healthy",
    "reset_clients",
    "role_url",
    "safe_call",
    "url_with_db",
]
