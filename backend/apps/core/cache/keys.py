"""Deterministic cache keys and surrogate-key derivation (PERFORMANCE §5).

A key has three parts:

* a **namespace** (``match:scorecard``, ``players`` …);
* **path parts** (ids) that are percent-encoded so a ``:`` or ``/`` inside an
  id can never make two different resources collide;
* **normalized query params** (sorted, ``None`` dropped, booleans as 0/1).

The rendered key includes a **version** supplied by :mod:`apps.core.cache.versions`
so invalidation is a version bump rather than a scan/delete. ``version_ns`` is
also the tracked resource (``match:<id>``, ``players``) and its surrogate-key
form (``match-<id>``) is what a CDN purge targets.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

#: Prefix inside Django's own cache key prefix (``cricket:`` in settings).
KEY_PREFIX = "cache"


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def normalize_params(params: Mapping[str, Any] | None) -> tuple[tuple[str, str], ...]:
    """Return query params as a sorted, deterministic tuple.

    ``None`` means "not supplied" and is dropped; empty strings are kept (they
    change endpoint behaviour); sequences keep their order and are joined with
    commas so ``?team=a&team=b`` is stable.
    """
    normalized: list[tuple[str, str]] = []
    for name, value in (params or {}).items():
        if value is None:
            continue
        if isinstance(value, (list, tuple, set, frozenset)):
            text = ",".join(_scalar(item) for item in value)
        else:
            text = _scalar(value)
        normalized.append((str(name), text))
    return tuple(sorted(normalized))


def surrogate_for(version_ns: str) -> str:
    """CDN surrogate key for a version namespace (``match:m1`` -> ``match-m1``)."""
    return version_ns.replace(":", "-")


@dataclass(frozen=True)
class CacheKey:
    """An unrendered cache key plus its invalidation identity."""

    namespace: str
    parts: tuple[str, ...]
    params: tuple[tuple[str, str], ...]
    version_ns: str
    surrogates: tuple[str, ...]

    @property
    def base(self) -> str:
        """The key without a version (stable across invalidations)."""
        segments = [KEY_PREFIX, self.namespace, *(quote(part, safe="") for part in self.parts)]
        base = ":".join(segments)
        if self.params:
            query = "&".join(
                f"{quote(name, safe='')}={quote(value, safe=',')}" for name, value in self.params
            )
            return f"{base}?{query}"
        return base

    def render(self, *, version: int) -> str:
        """The physical cache key for a given invalidation version."""
        head = f"{KEY_PREFIX}:{self.namespace}:v{version}"
        tail = "".join(f":{quote(part, safe='')}" for part in self.parts)
        key = head + tail
        if self.params:
            query = "&".join(
                f"{quote(name, safe='')}={quote(value, safe=',')}" for name, value in self.params
            )
            key = f"{key}?{query}"
        return key


def make_key(
    namespace: str,
    *parts: Any,
    params: Mapping[str, Any] | None = None,
    version_ns: str | None = None,
    surrogate: str | Sequence[str] | None = None,
) -> CacheKey:
    """Build a :class:`CacheKey`.

    ``version_ns`` defaults to the namespace. ``surrogate`` overrides the
    derived surrogate key(s); pass a sequence to tag one entry with several
    resources (e.g. a match list item that depends on a team).
    """
    if surrogate is None:
        surrogates: tuple[str, ...] = (surrogate_for(version_ns or namespace),)
    elif isinstance(surrogate, str):
        surrogates = (surrogate,)
    else:
        surrogates = tuple(surrogate)
    return CacheKey(
        namespace=namespace,
        parts=tuple(str(part) for part in parts),
        params=normalize_params(params),
        version_ns=version_ns or namespace,
        surrogates=surrogates,
    )


__all__ = [
    "KEY_PREFIX",
    "CacheKey",
    "make_key",
    "normalize_params",
    "surrogate_for",
]
