"""Deprecation metadata registry + ``Sunset`` header middleware (Task 7 §11.2).

The API spec promises deprecation headers (`CRICKET_API_SPECIFICATION.md`:
"additive changes preferred; deprecation headers ``Sunset``") but no mechanism
existed. This module ships the mechanism additively:

* a :class:`DeprecationMeta` record per **view_name** (endpoint) declaring the
  ``sunset_at`` ISO date, the ``replacement`` successor and whether the
  endpoint is already ``gone``;
* a :class:`DeprecationMiddleware` that appends ``Sunset: <date>`` and
  ``Link: <replacement>; rel="successor-version"`` to matching responses and
  answers ``410 Gone`` for fully-sunset endpoints, **without touching the
  response body** (additive-only compatibility — old fields are never removed).

No endpoints are deprecated in this phase; the tests register a deprecated
view_name to prove the headers, the ``410`` path, and that existing payloads
are unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse

from apps.core.errors import error_payload

SUNSET_HEADER = "Sunset"
LINK_HEADER = "Link"
#: RFC 8288 ``rel`` for a successor-version link.
SUCCESSOR_REL = "successor-version"
#: Registry keyed by view_name (e.g. ``matches:match-detail``).
_registry: dict[str, DeprecationMeta] = {}


@dataclass(frozen=True)
class DeprecationMeta:
    """One endpoint's deprecation policy."""

    #: The view_name (``app:name``) this policy applies to.
    endpoint: str
    #: ISO date the ``Sunset`` header carries (``None`` = deprecated but not
    #: yet scheduled — header omitted until a date is set).
    sunset_at: date | None = None
    #: Successor endpoint URL path (``/api/v2/...``) or endpoint name.
    replacement: str | None = None
    #: Fully sunset: the middleware answers ``410 Gone`` before the view runs.
    gone: bool = False


def register(meta: DeprecationMeta) -> None:
    """Register ``meta``; duplicate view_names are replaced deliberately."""
    _registry[meta.endpoint] = meta


def meta_for(view_name: str | None) -> DeprecationMeta | None:
    """The policy for ``view_name``, or ``None`` when it is not deprecated."""
    if not view_name:
        return None
    return _registry.get(view_name)


def deprecations() -> dict[str, DeprecationMeta]:
    """A copy of the registry (read-only view for docs/tests)."""
    return dict(_registry)


def reset() -> None:
    """Clear the registry (test isolation)."""
    _registry.clear()


def _append_link(response: HttpResponse, replacement: str) -> None:
    """Append ``Link: <replacement>; rel="successor-version"`` to ``response``."""
    link = f'<{replacement}>; rel="{SUCCESSOR_REL}"'
    existing = response.get(LINK_HEADER)
    response[LINK_HEADER] = f"{existing}, {link}" if existing else link


class DeprecationMiddleware:
    """Append ``Sunset``/``Link`` headers and answer ``410`` for sunset endpoints.

    ``process_view`` runs after URL resolution (``request.resolver_match`` is
    set), so a fully-gone endpoint is answered before the view executes. The
    header-append in ``__call__`` runs after the view, when the resolver is
    still available, and never alters the body.
    """

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

    def _view_name(self, request: HttpRequest) -> str | None:
        match = getattr(request, "resolver_match", None)
        return getattr(match, "view_name", None)

    def process_view(
        self,
        request: HttpRequest,
        view_func: Any,
        view_args: tuple[Any, ...],
        view_kwargs: dict[str, Any],
    ) -> HttpResponse | None:
        """Return ``410 Gone`` for fully-sunset endpoints; never run the view."""
        meta = meta_for(self._view_name(request))
        if meta is not None and meta.gone:
            return JsonResponse(
                error_payload(
                    "gone",
                    f"endpoint {meta.endpoint} was sunset and is no longer served; "
                    "see the Sunset header history and docs/API_DEPRECATION_POLICY.md",
                ),
                status=410,
            )
        return None

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response: HttpResponse = self.get_response(request)
        meta = meta_for(self._view_name(request))
        if meta is not None and meta.sunset_at is not None:
            response[SUNSET_HEADER] = meta.sunset_at.isoformat()
            if meta.replacement:
                _append_link(response, meta.replacement)
        return response


__all__ = [
    "LINK_HEADER",
    "SUCCESSOR_REL",
    "SUNSET_HEADER",
    "DeprecationMeta",
    "DeprecationMiddleware",
    "deprecations",
    "meta_for",
    "register",
    "reset",
]
