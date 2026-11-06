"""Key-versioned invalidation (PERFORMANCE §5, TESTING §10).

Every cache key embeds a per-resource **version**. Invalidating a resource is a
single ``INCR`` on its version key; every derived key becomes unreachable at
once and ages out under its own TTL. This avoids the two classic failure modes
of key deletion: scanning/``KEYS`` in production, and a race where a read
repopulates a key that a concurrent write just deleted.

Versions are stored in the Phase E cache alias with **no expiry** (a reset
would resurrect stale entries). A cache outage degrades to version ``0`` and
must never raise into a request.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from apps.core.cache.backend import cache_backend

logger = logging.getLogger("apps.core.cache.versions")

_VERSION_KEY = "cachever:{namespace}"


def _key(namespace: str) -> str:
    return _VERSION_KEY.format(namespace=namespace)


def current(namespace: str) -> int:
    """Current invalidation version for ``namespace`` (0 when unknown)."""
    try:
        value = cache_backend().get(_key(namespace))
    except Exception:  # a broken cache must not break a read
        logger.warning("cache version read failed", extra={"namespace": namespace})
        return 0
    if value is None:
        return 0
    try:
        return int(value)
    except TypeError, ValueError:
        return 0


def bump(namespace: str) -> int:
    """Atomically advance ``namespace`` and return the new version."""
    backend = cache_backend()
    key = _key(namespace)
    try:
        # ``add`` is atomic: first bump creates the counter, later bumps incr it.
        if backend.add(key, 1, timeout=None):
            return 1
        return int(backend.incr(key))
    except ValueError:
        # The key disappeared between add and incr (eviction): restart at 1.
        backend.set(key, 1, timeout=None)
        return 1
    except Exception:  # a broken cache must not break the write path
        logger.warning("cache version bump failed", extra={"namespace": namespace})
        return 0


def bump_many(namespaces: Iterable[str]) -> dict[str, int]:
    """Bump every distinct namespace once (order preserved for determinism)."""
    result: dict[str, int] = {}
    for namespace in dict.fromkeys(namespaces):
        result[namespace] = bump(namespace)
    return result


__all__ = ["bump", "bump_many", "current"]
