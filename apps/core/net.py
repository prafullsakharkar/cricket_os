"""Trusted-proxy-aware client IP extraction (SECURITY §2, PERFORMANCE §10).

The public read API sits behind an edge proxy/CDN, so the immediate peer
(``REMOTE_ADDR``) is usually a proxy, not the client. ``X-Forwarded-For`` is
client-controlled and must never be trusted wholesale.

The trust model mirrors Django/ipware's ``NUM_PROXIES``: ``hops`` is the
number of trusted proxies directly in front of the origin. The *rightmost*
``hops`` entries of ``X-Forwarded-For`` are assumed to have been appended by
our own edge; the entry immediately to their left is the original client. A
header with fewer entries than ``hops`` (a direct client forging the chain) is
ignored in favour of ``REMOTE_ADDR``, as is any header containing a malformed
entry. With ``hops=0`` (the default) the header is ignored entirely.

Deployment contract (documented in docs/REALTIME_AND_CACHING.md): the edge
must strip or replace inbound ``X-Forwarded-For`` and append exactly one entry
per trusted hop; ``TRUSTED_PROXY_HOPS`` must equal the real proxy count. Under
that contract a client can never impersonate a hop it did not traverse.
"""

from __future__ import annotations

import ipaddress
from typing import Any, cast

from django.conf import settings

UNKNOWN = "unknown"


def client_ip(
    request: Any,
    *,
    hops: int | None = None,
    forwarded_header: str = "HTTP_X_FORWARDED_FOR",
) -> str:
    """The client IP, or ``"unknown"`` when no address is available.

    ``hops`` overrides ``settings.TRUSTED_PROXY_HOPS`` when given (used by
    rate-limit scoping and tests).
    """
    remote = cast(str, request.META.get("REMOTE_ADDR") or UNKNOWN)
    hop_count = settings.TRUSTED_PROXY_HOPS if hops is None else hops
    if hop_count <= 0:
        return remote

    raw = request.META.get(forwarded_header)
    if not raw:
        return remote
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) <= hop_count:
        # A chain this short cannot have come through ``hop_count`` trusted
        # proxies: a direct client forged it.
        return remote
    candidate = cast(str, parts[len(parts) - 1 - hop_count])
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        # Malformed entries mean the header is forged/truncated: never trust it.
        return remote
    return candidate


__all__ = ["UNKNOWN", "client_ip"]
