"""Cache purging: local version bumps or CDN surrogate-key purges.

A CDN sees opaque ``Surrogate-Key`` tags, not our version namespaces. The
local purger bumps versions (the same effect as an event invalidation); the
HTTP purger issues a ``PURGE`` for the tags. The transport is injectable so
tests never open a socket, and production can swap in a provider client.
"""

from __future__ import annotations

import logging
import urllib.request
from collections.abc import Callable, Iterable
from typing import Any, Protocol

from apps.core import metrics
from apps.core.cache import versions

logger = logging.getLogger("apps.core.cache.purger")

Transport = Callable[..., Any]


class CachePurger(Protocol):
    """Anything that can invalidate a set of resource keys/tags."""

    def purge(self, keys: Iterable[str]) -> None: ...


class LocalCachePurger:
    """Invalidate in-process cache entries by bumping their versions."""

    def purge(self, keys: Iterable[str]) -> None:
        materialized = list(dict.fromkeys(keys))
        if not materialized:
            return
        versions.bump_many(materialized)
        metrics.CACHE_PURGES.inc(len(materialized))


class NoopCachePurger:
    """Accept and ignore purge requests (the default when no CDN is present).

    Origin invalidation is handled by the version-bump registry; this is the
    inert CDN hook so completion/correction events have no side effects beyond
    the local registry.
    """

    def purge(self, keys: Iterable[str]) -> None:
        return


class RecordingCachePurger:
    """Test double: record every purge call (deduplicated per call)."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def purge(self, keys: Iterable[str]) -> None:
        self.calls.append(list(dict.fromkeys(keys)))


def _urllib_transport(method: str, url: str, *, headers: dict[str, str], timeout: float) -> Any:
    request = urllib.request.Request(url, method=method, headers=headers)
    return urllib.request.urlopen(request, timeout=timeout)


class HttpCachePurger:
    """Purge CDN objects by surrogate key (one ``PURGE`` per batch)."""

    def __init__(
        self,
        url: str,
        *,
        transport: Transport | None = None,
        timeout: float = 2.0,
    ) -> None:
        self._url = url
        self._transport = transport or _urllib_transport
        self._timeout = timeout

    def purge(self, keys: Iterable[str]) -> None:
        materialized = list(dict.fromkeys(keys))
        if not materialized:
            return
        try:
            self._transport(
                "PURGE",
                self._url,
                headers={"Surrogate-Key": " ".join(materialized)},
                timeout=self._timeout,
            )
        except Exception:  # a CDN failure must not fail the write that triggered it
            logger.warning("cdn purge failed", extra={"surrogate_keys": materialized})
            return
        metrics.CACHE_PURGES.inc(len(materialized))


def for_settings(*, cdn_url: str = "", transport: Transport | None = None) -> CachePurger:
    """Local purger unless a CDN purge URL is configured."""
    if cdn_url:
        return HttpCachePurger(cdn_url, transport=transport)
    return LocalCachePurger()


__all__ = [
    "CachePurger",
    "HttpCachePurger",
    "LocalCachePurger",
    "NoopCachePurger",
    "RecordingCachePurger",
    "Transport",
    "for_settings",
]
