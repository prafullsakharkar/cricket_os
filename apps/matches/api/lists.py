"""Match list feeds: ``/matches/live|upcoming|recent`` (API_SPEC §3.1).

Three read-only feeds, each cursor-paginated and state-dependently cached:

* ``GET /matches/live`` — currently-live matches, newest start first,
  ``Cache-Control: public, max-age=5, stale-while-revalidate=5``;
* ``GET /matches/upcoming`` — scheduled fixtures in a window, soonest first,
  ``public, max-age=30, stale-while-revalidate=60``;
* ``GET /matches/recent`` — recently finished matches, newest first, same
  30s/60s cache.

Each item is the same shell as ``GET /matches/{id}/`` plus the licence flags
(``ball_data``/``commentary``/``attribution``) and ``stale``. ``stale`` is
computed from the live ingestion circuit breakers (SECURITY §8): when the
provider feeding a match has an OPEN breaker the API serves last-known-good
with ``meta.stale=true`` instead of failing. The collection ``meta`` carries
``limit``/``has_more``/``next_cursor`` from the cursor paginator plus the
aggregate ``stale`` flag.

Filters: ``format``, ``gender``, ``series``, ``team`` (all optional).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.cache.policy import for_fixtures, for_live
from apps.core.cache_views import CachedReadMixin
from apps.core.pagination import CursorPagination
from apps.live.services.breaker import is_open_for
from apps.matches.selectors.reads import (
    list_live_matches,
    list_recent_matches,
    list_upcoming_matches,
    match_summaries,
)
from apps.providers.models import DataProvider


class _MatchListMixin(CachedReadMixin, APIView):
    """Cursor-paginated match feed; subclasses pick the selector + ordering.

    Each feed is cached under its own version namespace
    (``matches:live|upcoming|recent``) so a state-change event bumps exactly
    the buckets it affects.
    """

    selector: Callable[..., Any]
    ordering: Sequence[str] = ("-start_time_utc",)

    def _filters(self, request: Any) -> dict[str, Any]:
        return {
            "format_type": request.query_params.get("format"),
            "gender": request.query_params.get("gender"),
            "series_id": request.query_params.get("series"),
            "team_id": request.query_params.get("team"),
        }

    def _stale_codes(self, rows: Sequence[Any]) -> frozenset[str]:
        """Provider codes with an OPEN circuit breaker on this page."""
        provider_ids = {m.source_provider_id for m in rows if m.source_provider_id}
        if not provider_ids:
            return frozenset()
        codes = DataProvider.objects.filter(id__in=provider_ids).values_list("code", flat=True)
        return frozenset(code for code in codes if is_open_for(code))

    def get(self, request: Any) -> Response:
        # ``self.selector`` would bind the plain function as a method, so read
        # it off the class (subclasses assign the selector as a class attr).
        selector = type(self).selector
        queryset = selector(**self._filters(request))
        paginator = CursorPagination()
        paginator.ordering = self.ordering
        rows = paginator.paginate_queryset(queryset, request)

        stale_codes = self._stale_codes(rows)
        items = match_summaries(rows, stale_codes=stale_codes)
        return Response(
            {
                "data": items,
                "meta": {
                    "limit": paginator.page_size_value,
                    "has_more": paginator.has_more,
                    "next_cursor": paginator.next_cursor,
                    "stale": bool(stale_codes),
                },
            }
        )


class LiveMatchListView(_MatchListMixin):
    """``GET /matches/live`` — currently-live matches (seconds-level cache)."""

    selector = list_live_matches
    ordering = ("-start_time_utc",)
    cache_namespace = "matches:live"

    def cache_plan(self) -> Any:
        return for_live()


class UpcomingMatchListView(_MatchListMixin):
    """``GET /matches/upcoming`` — fixtures in the next window."""

    selector = list_upcoming_matches
    ordering = ("start_time_utc",)
    cache_namespace = "matches:upcoming"

    def cache_plan(self) -> Any:
        return for_fixtures()


class RecentMatchListView(_MatchListMixin):
    """``GET /matches/recent`` — recently finished matches."""

    selector = list_recent_matches
    ordering = ("-end_time_utc",)
    cache_namespace = "matches:recent"

    def cache_plan(self) -> Any:
        return for_fixtures()


__all__ = [
    "LiveMatchListView",
    "RecentMatchListView",
    "UpcomingMatchListView",
]
