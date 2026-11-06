"""Read-through response caching for read-only views (PERFORMANCE §5).

:class:`CachedReadMixin` wraps ``dispatch`` so that a cacheable ``GET`` is
served from the Phase E cache alias, bypassing serializers and the database
entirely on a hit. What is stored is the **rendered response body** plus its
strong ETag, the ``Cache-Control`` policy that was current when it was built,
the surrogate keys, and the captured ``meta.event_seq`` — never ORM objects.

* On a miss the request flows through the normal DRF stack once
  (:class:`apps.core.cache.store` single-flight means N concurrent misses
  cause one build).
* On a hit the body and headers are reproduced byte-for-byte.
* ``If-None-Match`` answers ``304`` with the cache headers preserved.
* 3xx/4xx/5xx and streaming responses are never cached.
* A Redis outage degrades to a direct build (never an error).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from django.conf import settings
from django.http import HttpResponse
from rest_framework.response import Response

from apps.core.cache import keys, policy, store
from apps.core.cache.policy import CachePlan
from apps.core.http import compute_etag
from apps.core.throttling import TokenBucketThrottle


def surrogate_header_name() -> str:
    """The header carrying the response's surrogate keys (configurable)."""
    return getattr(settings, "CACHE_SURROGATE_HEADER", "Surrogate-Key")


@dataclass(frozen=True)
class CachedPayload:
    """Everything needed to reproduce a response exactly.

    For ``cacheable`` payloads the headers are regenerated deterministically
    from the stored fields. For non-cacheable ones (3xx/4xx/5xx, empty bodies)
    ``headers`` carries the original response headers (minus ``Content-Length``,
    which Django recomputes) so redirects and error envelopes survive intact.
    """

    body: bytes
    status: int
    content_type: str
    etag: str
    cache_control: str
    vary: str
    surrogate_keys: tuple[str, ...]
    event_seq: int | None
    cacheable: bool = True
    headers: tuple[tuple[str, str], ...] = ()


class CachedReadMixin:
    """Serve cacheable GETs from the cache; build-on-miss for the rest."""

    #: Resource namespace (``match:shell``, ``matches:live``, ``teams`` …).
    cache_namespace: ClassVar[str] = ""

    #: Token-bucket throttle scope (API_SPEC §7); the ``deliveries`` view
    #: overrides this with a stricter bucket.
    throttle_scope: ClassVar[str] = "default"

    def get_throttles(self) -> list[Any]:
        """Every cached read view is token-bucket throttled.

        Overrides ``APIView.get_throttles`` so the throttle applies before the
        cache lookup (cache hits are rate-limited too) without declaring a
        ``throttle_classes`` attribute that conflicts with DRF's.
        """
        return [TokenBucketThrottle()]

    def cache_parts(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        """Path parts that identify the resource (default: none)."""
        return ()

    def cache_version_ns(self, request: Any, *args: Any, **kwargs: Any) -> str:
        """The invalidation namespace (defaults to the resource namespace)."""
        return self.cache_namespace

    def cache_surrogates(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        """Surrogate keys for this response (default: derived from the ns)."""
        return (keys.surrogate_for(self.cache_version_ns(request, *args, **kwargs)),)

    def cache_plan(self) -> CachePlan:
        """The cache policy; resolved only when a build happens."""
        return policy.for_reference()

    def cache_key(self, request: Any, *args: Any, **kwargs: Any) -> keys.CacheKey | None:
        """The cache key, or ``None`` when this request must not be cached.

        ``dispatch`` receives a plain ``WSGIRequest`` (DRF initialises its own
        ``Request`` inside ``APIView.dispatch``), so read params in a way that
        works for both.
        """
        if request.method != "GET" or not self.cache_namespace:
            return None
        params = getattr(request, "query_params", None) or request.GET
        return keys.make_key(
            self.cache_namespace,
            *self.cache_parts(request, *args, **kwargs),
            params=params,
            version_ns=self.cache_version_ns(request, *args, **kwargs),
            surrogate=self.cache_surrogates(request, *args, **kwargs),
        )

    def dispatch(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """DRF lifecycle with the cache inserted between checks and handler.

        Mirror ``APIView.dispatch`` so the throttle/authentication checks run
        *before* the cache lookup (cache hits are rate-limited too) and every
        exception — including ``Throttled`` — is enveloped by the DRF handler.
        The handler runs only on a cache miss.
        """
        self.args = args
        self.kwargs = kwargs
        # The concrete view base (ListAPIView/RetrieveAPIView/APIView) provides
        # these members; the mixin is typed independently so they are ignored.
        request = self.initialize_request(request, *args, **kwargs)  # type: ignore[attr-defined]
        self.request = request
        self.headers = self.default_response_headers  # type: ignore[attr-defined]

        try:
            self.initial(request, *args, **kwargs)  # type: ignore[attr-defined]
        except Exception as exc:
            response = self.handle_exception(exc)  # type: ignore[attr-defined]
            self.response = self.finalize_response(  # type: ignore[attr-defined]
                request, response, *args, **kwargs
            )
            return self.response

        not_allowed = "http_method_not_allowed"
        handler = getattr(self, request.method.lower(), getattr(self, not_allowed))
        try:
            key = self.cache_key(request, *args, **kwargs)
            if key is None:
                response = handler(request, *args, **kwargs)
            else:

                def build() -> CachedPayload:
                    return self._render_payload(handler, request, args, kwargs, key)

                result = store.get_or_build(
                    key,
                    build,
                    plan=self.cache_plan,
                    event_seq=lambda: getattr(self, "_event_seq", None),
                )
                response = self._respond(request, result.value)
        except Exception as exc:
            response = self.handle_exception(exc)  # type: ignore[attr-defined]

        self.response = self.finalize_response(  # type: ignore[attr-defined]
            request, response, *args, **kwargs
        )
        return self.response

    def _render_payload(
        self,
        handler: Any,
        request: Any,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        key: keys.CacheKey,
    ) -> CachedPayload:
        """Run the view handler, render the response, and capture it."""
        response = handler(request, *args, **kwargs)
        if isinstance(response, Response):
            # The handler returns an un-finalized DRF Response; give it the
            # negotiated renderer (as ``finalize_response`` would) so
            # ``rendered_content`` works below.
            if not getattr(request, "accepted_renderer", None):
                neg = self.perform_content_negotiation(  # type: ignore[attr-defined]
                    request, force=True
                )
                request.accepted_renderer, request.accepted_media_type = neg
            response.accepted_renderer = request.accepted_renderer
            response.accepted_media_type = request.accepted_media_type
            response.renderer_context = self.get_renderer_context()  # type: ignore[attr-defined]
        status = response.status_code
        # Accessing ``rendered_content`` is what sets ``Content-Type`` on a DRF
        # Response, so force the render before reading the header.
        body = getattr(response, "rendered_content", None)
        if body is None:
            body = response.content
        if isinstance(body, str):
            body = body.encode("utf-8")
        body = bytes(body)
        content_type = response.get("Content-Type", "application/json")
        event_seq = getattr(self, "_event_seq", None)

        if status >= 300 or not body:
            return CachedPayload(
                body=body,
                status=status,
                content_type=content_type,
                etag="",
                cache_control="",
                vary="",
                surrogate_keys=(),
                event_seq=event_seq,
                cacheable=False,
                headers=tuple(
                    (name, value)
                    for name, value in response.headers.items()
                    if name.lower() != "content-length"
                ),
            )

        plan = self.cache_plan()
        return CachedPayload(
            body=body,
            status=status,
            content_type=content_type,
            etag=compute_etag(body),
            cache_control=plan.cache_control(),
            vary="Accept-Encoding",
            surrogate_keys=key.surrogates,
            event_seq=event_seq,
            cacheable=True,
        )

    def _respond(self, request: Any, payload: CachedPayload) -> HttpResponse:
        if not payload.cacheable:
            response = HttpResponse(
                payload.body, status=payload.status, content_type=payload.content_type or None
            )
            for name, value in payload.headers:
                if name.lower() != "content-length":
                    response[name] = value
            return response
        response = HttpResponse(
            payload.body, status=payload.status, content_type=payload.content_type
        )
        response["Cache-Control"] = payload.cache_control
        response["ETag"] = payload.etag
        if payload.vary:
            response["Vary"] = payload.vary
        if payload.surrogate_keys:
            response[surrogate_header_name()] = " ".join(payload.surrogate_keys)

        if request.META.get("HTTP_IF_NONE_MATCH") == payload.etag:
            not_modified = HttpResponse(status=304)
            for header in ("Cache-Control", "ETag", "Vary"):
                if header in response:
                    not_modified[header] = response[header]
            if payload.surrogate_keys:
                not_modified[surrogate_header_name()] = " ".join(payload.surrogate_keys)
            return not_modified
        return response


__all__ = ["CachedPayload", "CachedReadMixin", "surrogate_header_name"]
