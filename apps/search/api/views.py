"""``GET /search`` — cross-entity search (CRICKET_API_SPECIFICATION §2.6).

Runs the Group 6 search service with its safety gates; a bad query (too
short/long, unknown type) is a ``400 invalid_query``. Cached ``max-age=30``
under the ``search`` version namespace.
"""

from __future__ import annotations

from typing import Any

from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.cache.policy import CachePlan
from apps.core.cache_views import CachedReadMixin
from apps.core.errors import InvalidQuery
from apps.search.services.search import cross_entity_search


class SearchView(CachedReadMixin, APIView):
    """``GET /search?q=&types=`` (max-age=30)."""

    cache_namespace = "search"

    def cache_plan(self) -> Any:
        return CachePlan(redis_ttl=30, max_age=30)

    def get(self, request: Any) -> Response:
        params = request.query_params
        q = params.get("q", "")
        types = params.getlist("types") or None
        try:
            rows = cross_entity_search(
                q,
                types=tuple(types) if types else None,
                limit=int(params.get("limit", 20)),
            )
        except ValueError as exc:
            raise InvalidQuery(str(exc)) from exc
        return Response({"data": rows, "meta": {"query": q, "count": len(rows)}})


__all__ = ["SearchView"]
