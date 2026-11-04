"""Token-bucket rate limiting (API_SPEC §7, PERFORMANCE §10, SECURITY §2).

Components:

* :class:`TokenBucket` — pure bucket maths, tested with a virtual clock;
* :class:`BucketStore` — persistence; the :class:`RedisBucketStore` lives on
  the ``ratelimit`` Redis role (``noeviction``) and :class:`InProcessBucketStore`
  is the per-process fallback used when Redis is unhealthy. Rate limiting is
  best-effort: a broken store degrades to the local fallback + a metric
  (recorded decision #8 — availability over strictness for public reads);
* :class:`RateLimiter` — per-scope buckets keyed by client IP, or by API key
  when a :class:`KeyResolver` is configured (interface + header hook only; key
  issuance is out of scope);
* :class:`TokenBucketThrottle` — the DRF hook; ``CachedReadMixin`` runs it
  *before* the cache lookup so cache hits are throttled too;
* :class:`ConnectionLimiter` — per-node connection caps for the SSE/WS connect
  endpoints (Groups 7/8): fail-open (never errors) but bounded per node.

Scope rates come from ``RATELIMIT_RATES`` (``scope -> (tokens_per_second,
burst)``); the ``deliveries`` scope is stricter than ``default`` per spec §7.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from django.conf import settings

from apps.core import metrics
from apps.core.net import client_ip

logger = logging.getLogger("apps.core.throttling")

#: Fallback when a scope has no configured rate (generous, never a surprise).
DEFAULT_RATE = (10.0, 120.0)
_FALLBACK_MAX_BUCKETS = 100_000
_BUCKET_TTL_SECONDS = 60


# --------------------------------------------------------------------------- #
# Token bucket maths
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TokenBucket:
    """A refilling token bucket (``rate`` tokens/s, capacity ``burst``)."""

    rate: float
    burst: float
    tokens: float
    updated_at: float

    @classmethod
    def fresh(cls, rate: float, burst: float, now: float) -> TokenBucket:
        return cls(rate=rate, burst=burst, tokens=burst, updated_at=now)

    def refilled(self, now: float) -> TokenBucket:
        elapsed = max(0.0, now - self.updated_at)
        tokens = min(self.burst, self.tokens + elapsed * self.rate)
        return TokenBucket(self.rate, self.burst, tokens, now)

    def try_consume(self, now: float, cost: float = 1.0) -> tuple[bool, float | None, TokenBucket]:
        """Consume ``cost`` tokens; return ``(allowed, retry_after, bucket)``."""
        bucket = self.refilled(now)
        if bucket.tokens >= cost:
            remaining = TokenBucket(
                bucket.rate, bucket.burst, bucket.tokens - cost, bucket.updated_at
            )
            return True, None, remaining
        retry = max(0.0, math.ceil((cost - bucket.tokens) / bucket.rate))
        return False, retry, bucket


# --------------------------------------------------------------------------- #
# Stores
# --------------------------------------------------------------------------- #


class BucketStore(Protocol):
    """Anything that can persist token buckets."""

    def get(self, key: str) -> TokenBucket | None: ...
    def set(self, key: str, bucket: TokenBucket) -> None: ...


class InProcessBucketStore:
    """Per-process bucket store (test default and Redis-down fallback)."""

    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> TokenBucket | None:
        with self._lock:
            return self._buckets.get(key)

    def set(self, key: str, bucket: TokenBucket) -> None:
        with self._lock:
            # Bound the fallback: an attacker hammering unique keys must not
            # grow memory without limit (crude clear; correctness never depends
            # on the fallback being globally consistent).
            if len(self._buckets) >= _FALLBACK_MAX_BUCKETS:
                self._buckets.clear()
            self._buckets[key] = bucket


class RedisBucketStore:
    """Buckets in the ``ratelimit`` Redis role as JSON blobs with a TTL.

    ``client`` is injectable so tests never open a socket. Reads/writes are
    best-effort: any Redis error propagates to the limiter, which degrades to
    the in-process fallback.
    """

    def __init__(self, client: Any | None = None, *, ttl: int = _BUCKET_TTL_SECONDS) -> None:
        self._client = client
        self._ttl = ttl

    def _conn(self) -> Any:
        if self._client is not None:
            return self._client
        from apps.core.redis import ROLE_RATELIMIT, client

        return client(ROLE_RATELIMIT)

    def get(self, key: str) -> TokenBucket | None:
        raw = self._conn().get(f"rl:{key}")
        if raw is None:
            return None
        data = json.loads(raw)
        return TokenBucket(
            rate=float(data["rate"]),
            burst=float(data["burst"]),
            tokens=float(data["tokens"]),
            updated_at=float(data["updated_at"]),
        )

    def set(self, key: str, bucket: TokenBucket) -> None:
        payload = json.dumps(
            {
                "rate": bucket.rate,
                "burst": bucket.burst,
                "tokens": bucket.tokens,
                "updated_at": bucket.updated_at,
            }
        )
        self._conn().set(f"rl:{key}", payload, ex=self._ttl)


# --------------------------------------------------------------------------- #
# Identity: client IP or API key
# --------------------------------------------------------------------------- #


class KeyResolver(Protocol):
    """Resolve an API key from a request (interface; no issuance/user accounts)."""

    def resolve(self, request: Any) -> str | None: ...


class HeaderKeyResolver:
    """Read a key from a request header (the default header hook)."""

    def __init__(self, header: str = "HTTP_X_API_KEY") -> None:
        self._header = header

    def resolve(self, request: Any) -> str | None:
        value = request.META.get(self._header)
        return str(value).strip() if value else None


def _key_resolver() -> KeyResolver | None:
    header = getattr(settings, "RATELIMIT_KEY_HEADER", "") or ""
    return HeaderKeyResolver(header) if header else None


# --------------------------------------------------------------------------- #
# Limiter
# --------------------------------------------------------------------------- #


class RateLimiter:
    """Enforce per-scope, per-identity token buckets with a fail-open fallback."""

    def __init__(
        self,
        store: BucketStore | None = None,
        *,
        rates: Mapping[str, tuple[float, float]] | None = None,
        resolver: KeyResolver | None = None,
        fallback: BucketStore | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._primary = store or _default_store()
        self._fallback = fallback or InProcessBucketStore()
        self._rates = dict(rates or {})
        self._resolver = resolver
        self._clock = clock
        self.degraded = False

    def allow(self, scope: str, identity: str, *, cost: float = 1.0) -> tuple[bool, float | None]:
        """Consume one token for ``identity`` in ``scope``.

        Returns ``(allowed, retry_after_seconds)``. Never raises: a broken
        store falls back to the per-process store and records the degradation.
        """
        rate, burst = self._rates.get(scope, DEFAULT_RATE)
        key = f"{scope}:{identity}"
        now = self._clock()
        try:
            bucket = self._primary.get(key)
            fresh = (
                bucket.refilled(now) if bucket is not None else TokenBucket.fresh(rate, burst, now)
            )
            allowed, retry_after, updated = fresh.try_consume(now, cost)
            self._primary.set(key, updated)
        except Exception:
            return self._allow_fallback(key, rate, burst, cost, now)
        if allowed:
            self.degraded = False
        return allowed, retry_after

    def _allow_fallback(
        self, key: str, rate: float, burst: float, cost: float, now: float
    ) -> tuple[bool, float | None]:
        if not self.degraded:
            self.degraded = True
            metrics.RATE_LIMIT_FALLBACK.inc()
            logger.warning("rate-limit store unavailable; using per-process fallback")
        bucket = self._fallback.get(key)
        fresh = bucket.refilled(now) if bucket is not None else TokenBucket.fresh(rate, burst, now)
        allowed, retry_after, updated = fresh.try_consume(now, cost)
        self._fallback.set(key, updated)
        return allowed, retry_after


def _default_store() -> BucketStore:
    backend = getattr(settings, "RATELIMIT_BACKEND", "redis")
    if backend == "in_process":
        return InProcessBucketStore()
    return RedisBucketStore()


_limiter: RateLimiter | None = None


def get_limiter() -> RateLimiter:
    """The process limiter, built from settings on first use."""
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(
            resolver=_key_resolver(),
            rates=getattr(settings, "RATELIMIT_RATES", None),
        )
    return _limiter


def configure_limiter(limiter: RateLimiter | None) -> None:
    """Swap the process limiter (tests inject fakes)."""
    global _limiter
    _limiter = limiter


def reset_limiter() -> None:
    """Drop the process limiter so the next call rebuilds from settings."""
    global _limiter
    _limiter = None


_connection_limiter: ConnectionLimiter | None = None


def get_connection_limiter() -> ConnectionLimiter:
    """The process-wide connection cap counter (SSE/WS transports, per node).

    One shared instance means a client's SSE and WebSocket connections (Group
    8) draw against the same per-node budget; the counters are keyed by
    transport prefix so per-transport caps stay independent.
    """
    global _connection_limiter
    if _connection_limiter is None:
        _connection_limiter = ConnectionLimiter()
    return _connection_limiter


def configure_connection_limiter(limiter: ConnectionLimiter | None) -> None:
    """Swap the process connection limiter (tests inject fakes)."""
    global _connection_limiter
    _connection_limiter = limiter


def reset_connection_limiter() -> None:
    """Drop the process connection limiter (test isolation)."""
    global _connection_limiter
    _connection_limiter = None


# --------------------------------------------------------------------------- #
# DRF throttle
# --------------------------------------------------------------------------- #


class TokenBucketThrottle:
    """DRF throttle over the token-bucket limiter.

    The bucket identity is the resolved API key when present, else the
    trusted-proxy client IP. ``wait()`` returns the seconds until the next
    token so DRF emits ``Retry-After`` on the ``429``.
    """

    def allow_request(self, request: Any, view: Any) -> bool:
        scope = getattr(view, "throttle_scope", "default")
        identity = _identity(request)
        allowed, retry_after = get_limiter().allow(scope, identity)
        self._retry_after = retry_after if not allowed else None
        if not allowed:
            metrics.RATE_LIMIT_DENIED.labels(scope=scope).inc()
        return allowed

    def wait(self) -> float | None:
        return self._retry_after


def _identity(request: Any) -> str:
    resolver = _key_resolver()
    api_key = resolver.resolve(request) if resolver is not None else None
    if api_key:
        return f"key:{api_key}"
    return f"ip:{client_ip(request)}"


# --------------------------------------------------------------------------- #
# Connection caps (SSE/WS connect endpoints)
# --------------------------------------------------------------------------- #


class ConnectionLimiter:
    """Per-node, per-key connection counting (never raises, bounded counts).

    Used by the realtime transport endpoints to cap connections per IP, per
    match and per node. ``try_acquire`` is fail-open in the sense that it
    cannot error; the bound is the in-memory count, which is per process.
    """

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def try_acquire(self, key: str, *, limit: int) -> bool:
        with self._lock:
            current = self._counts.get(key, 0)
            if current >= limit:
                return False
            self._counts[key] = current + 1
            return True

    def release(self, key: str) -> None:
        with self._lock:
            current = self._counts.get(key, 0)
            if current <= 1:
                self._counts.pop(key, None)
            else:
                self._counts[key] = current - 1

    def count(self, key: str) -> int:
        with self._lock:
            return self._counts.get(key, 0)


__all__ = [
    "BucketStore",
    "ConnectionLimiter",
    "HeaderKeyResolver",
    "InProcessBucketStore",
    "KeyResolver",
    "RateLimiter",
    "RedisBucketStore",
    "TokenBucket",
    "TokenBucketThrottle",
    "configure_connection_limiter",
    "configure_limiter",
    "get_connection_limiter",
    "get_limiter",
    "reset_connection_limiter",
    "reset_limiter",
]
