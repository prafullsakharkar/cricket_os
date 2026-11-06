"""Phase E cache package (PERFORMANCE §5, API_SPECIFICATION §3.9).

Public surface:

* :func:`keys.make_key` — deterministic keys with a version namespace;
* :mod:`policy` — state-dependent Redis/HTTP TTL plans;
* :func:`store.get_or_build` — single-flight, SWR, breaker-backed read-through;
* :func:`versions.bump` — key-versioned invalidation;
* :mod:`invalidation` — event topic -> namespace registry;
* :mod:`purger` — local and CDN surrogate-key purging.
"""

from __future__ import annotations

from apps.core.cache import invalidation, keys, policy, purger, store, versions

__all__ = ["invalidation", "keys", "policy", "purger", "store", "versions"]
