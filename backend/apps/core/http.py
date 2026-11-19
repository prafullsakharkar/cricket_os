"""HTTP cache helpers implementing API_SPECIFICATION §3.9.

* :func:`compute_etag` — strong ``ETag`` (digest of the serialized payload).
* :func:`apply_cache_policy` — sets ``Cache-Control`` (with optional
  ``stale-while-revalidate``) + ``ETag`` and answers ``If-None-Match`` with
  ``304`` while preserving the cache headers.
* :class:`CacheControlMixin` — DRF view mixin; per-view TTLs come from the
  view's ``cache_max_age`` / ``cache_swr`` class attributes (never hard-coded
  here). 3xx/4xx/5xx and streaming responses are left untouched.
"""

from __future__ import annotations

import hashlib
from typing import Any

from django.http import HttpResponse
from rest_framework.response import Response


def compute_etag(payload: bytes | str) -> str:
    """Return a strong ETag (quoted hex digest) for a rendered payload."""
    raw = payload if isinstance(payload, bytes) else payload.encode("utf-8")
    return f'"{hashlib.sha256(raw).hexdigest()}"'


def apply_cache_policy(
    response: HttpResponse,
    *,
    max_age: int,
    swr: int | None = None,
    etag: str | None = None,
    request: Any = None,
) -> HttpResponse:
    """Set cache headers on ``response`` and honour ``If-None-Match``.

    When the request carries an ``If-None-Match`` equal to ``etag`` the
    response is replaced by an empty ``304`` that keeps the cache headers.
    """
    directives = ["public", f"max-age={max_age}"]
    if swr is not None:
        directives.append(f"stale-while-revalidate={swr}")
    response["Cache-Control"] = ", ".join(directives)
    if etag is not None:
        response["ETag"] = etag

    if request is not None and etag is not None and request.META.get("HTTP_IF_NONE_MATCH") == etag:
        not_modified = HttpResponse(status=304)
        not_modified["Cache-Control"] = response["Cache-Control"]
        not_modified["ETag"] = etag
        return not_modified
    return response


class CacheControlMixin:
    """Add the spec's cache policy to a DRF view.

    Set ``cache_max_age`` (seconds) and optionally ``cache_swr`` on the view.
    The strong ETag is computed from the rendered payload, so any content
    change invalidates it automatically.
    """

    cache_max_age: int | None = None
    cache_swr: int | None = None

    def finalize_response(self, request: Any, response: Response, *args: Any, **kwargs: Any) -> Any:
        response = super().finalize_response(request, response, *args, **kwargs)  # type: ignore[misc]
        if self.cache_max_age is None or response.status_code >= 300 or response.streaming:
            return response
        payload = getattr(response, "rendered_content", None)
        if payload is None:
            return response
        etag = compute_etag(payload)
        return apply_cache_policy(
            response,
            max_age=self.cache_max_age,
            swr=self.cache_swr,
            etag=etag,
            request=request,
        )


__all__ = ["CacheControlMixin", "apply_cache_policy", "compute_etag"]
