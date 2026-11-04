"""Read-through cache store: single-flight, SWR and graceful degradation.

Design goals (PERFORMANCE §5):

* **one build per miss storm** — a per-key process lock plus a re-check inside
  the lock means concurrent requests wait for the first builder instead of
  stampeding the database;
* **stale-while-revalidate** — entries carry a *soft* expiry (served fresh) and
  a *hard* expiry (backend TTL). Between them the last-known-good value is
  served immediately and one background refresh runs;
* **degradation** — a local breaker skips Redis entirely after repeated
  failures; the request is served by the builder. A cache outage costs latency,
  never availability.

The store is intentionally agnostic about payload shape: callers pass a builder
and receive a :class:`CacheResult`, optionally with the ``event_seq`` captured
with the snapshot (the REST -> subscribe hand-off, API_SPEC §3.8).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from apps.core import metrics
from apps.core.cache import keys, versions
from apps.core.cache.backend import cache_backend
from apps.core.cache.policy import CachePlan

logger = logging.getLogger("apps.core.cache.store")

#: Sentinel distinguishing "backend error" from "entry not found".
_ERROR = object()


class LocalBreaker:
    """A tiny per-process circuit breaker for the cache backend."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        recovery_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = failure_threshold
        self._recovery = recovery_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self._threshold and self._opened_at is None:
                self._opened_at = self._clock()

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def is_open(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return False
            return (self._clock() - self._opened_at) < self._recovery

    def allow_request(self) -> bool:
        return not self.is_open()


breaker = LocalBreaker()

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_refreshing: set[str] = set()
_refresh_guard = threading.Lock()


def reset_breaker() -> None:
    """Replace the process breaker (test isolation)."""
    global breaker
    breaker = LocalBreaker()
    _refresh_breaker_metric()


def _refresh_breaker_metric() -> None:
    metrics.CACHE_BREAKER_OPEN.set(1 if breaker.is_open() else 0)


def _lock_for(rendered: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(rendered)
        if lock is None:
            lock = threading.Lock()
            _locks[rendered] = lock
        return lock


@dataclass(frozen=True)
class CacheEntry:
    """Cached value plus its freshness metadata."""

    value: Any
    soft_expires_at: float
    hard_expires_at: float
    event_seq: int | None = None
    built_at: float = 0.0


@dataclass(frozen=True)
class CacheResult:
    """What a read produced."""

    value: Any
    hit: bool
    stale: bool = False
    event_seq: int | None = None
    degraded: bool = False


def _read(rendered: str) -> CacheEntry | object | None:
    try:
        entry = cache_backend().get(rendered)
    except Exception:
        breaker.record_failure()
        _refresh_breaker_metric()
        logger.warning("cache read failed", extra={"cache_key": rendered})
        return _ERROR
    breaker.record_success()
    _refresh_breaker_metric()
    if entry is None:
        return None
    if isinstance(entry, CacheEntry):
        return entry
    # A foreign/stale value type: treat as a miss and replace it.
    return None


def _spawn(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="cache-revalidate", daemon=True).start()


def _build[T](
    rendered: str,
    key: keys.CacheKey,
    builder: Callable[[], T],
    plan: CachePlan,
    event_seq: Callable[[], int | None] | None,
    clock: Callable[[], float],
) -> CacheResult:
    """Build and store under the per-key lock (re-checking first)."""
    with _lock_for(rendered):
        now = clock()
        entry = _read(rendered)
        if entry is not _ERROR and entry is not None:
            assert isinstance(entry, CacheEntry)
            if now < entry.soft_expires_at:
                metrics.CACHE_HITS.labels(namespace=key.namespace).inc()
                return CacheResult(
                    value=entry.value,
                    hit=True,
                    stale=False,
                    event_seq=entry.event_seq,
                )

        started = clock()
        value = builder()
        seq = event_seq() if event_seq is not None else None
        metrics.CACHE_BUILD_SECONDS.observe(max(0.0, clock() - started))

        soft = now + plan.redis_ttl
        hard = soft + (plan.swr or 0)
        fresh = CacheEntry(
            value=value,
            soft_expires_at=soft,
            hard_expires_at=hard,
            event_seq=seq,
            built_at=now,
        )
        try:
            cache_backend().set(rendered, fresh, timeout=max(1, int(hard - now)))
            breaker.record_success()
            _refresh_breaker_metric()
        except Exception:
            breaker.record_failure()
            _refresh_breaker_metric()
            logger.warning("cache write failed", extra={"cache_key": rendered})

        metrics.CACHE_MISSES.labels(namespace=key.namespace).inc()
        return CacheResult(value=value, hit=False, stale=False, event_seq=seq)


def _schedule_refresh[T](
    rendered: str,
    key: keys.CacheKey,
    builder: Callable[[], T],
    plan: CachePlan,
    event_seq: Callable[[], int | None] | None,
    clock: Callable[[], float],
    scheduler: Callable[[Callable[[], None]], None],
) -> None:
    with _refresh_guard:
        if rendered in _refreshing:
            return
        _refreshing.add(rendered)

    def run() -> None:
        try:
            _build(rendered, key, builder, plan, event_seq, clock)
        except Exception:  # background refresh must never escape the thread
            logger.warning("cache revalidation failed", extra={"cache_key": rendered})
        finally:
            with _refresh_guard:
                _refreshing.discard(rendered)

    try:
        scheduler(run)
    except Exception:  # pragma: no cover - scheduler failure is defensive
        with _refresh_guard:
            _refreshing.discard(rendered)


def _direct[T](
    builder: Callable[[], T],
    event_seq: Callable[[], int | None] | None,
    clock: Callable[[], float],
) -> CacheResult:
    """Serve without the cache (breaker open / backend error)."""
    metrics.CACHE_DEGRADED.inc()
    started = clock()
    value = builder()
    metrics.CACHE_BUILD_SECONDS.observe(max(0.0, clock() - started))
    seq = event_seq() if event_seq is not None else None
    return CacheResult(value=value, hit=False, stale=False, event_seq=seq, degraded=True)


def get_or_build[T](
    key: keys.CacheKey,
    builder: Callable[[], T],
    *,
    plan: CachePlan,
    event_seq: Callable[[], int | None] | None = None,
    clock: Callable[[], float] = time.time,
    scheduler: Callable[[Callable[[], None]], None] = _spawn,
    allow_stale: bool = True,
) -> CacheResult:
    """Return a cached value, building it via ``builder`` when needed."""
    if not breaker.allow_request():
        _refresh_breaker_metric()
        return _direct(builder, event_seq, clock)

    rendered = key.render(version=versions.current(key.version_ns))
    now = clock()
    entry = _read(rendered)

    if entry is _ERROR:
        return _direct(builder, event_seq, clock)

    if isinstance(entry, CacheEntry):
        if now < entry.soft_expires_at:
            metrics.CACHE_HITS.labels(namespace=key.namespace).inc()
            return CacheResult(value=entry.value, hit=True, stale=False, event_seq=entry.event_seq)
        if allow_stale and now < entry.hard_expires_at:
            metrics.CACHE_STALE.labels(namespace=key.namespace).inc()
            _schedule_refresh(rendered, key, builder, plan, event_seq, clock, scheduler)
            return CacheResult(value=entry.value, hit=True, stale=True, event_seq=entry.event_seq)

    return _build(rendered, key, builder, plan, event_seq, clock)


__all__ = [
    "CacheEntry",
    "CacheResult",
    "LocalBreaker",
    "breaker",
    "get_or_build",
    "reset_breaker",
]
