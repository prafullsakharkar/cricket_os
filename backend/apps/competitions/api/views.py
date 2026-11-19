"""Competition / Series list & detail views."""

from __future__ import annotations

from typing import Any

from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.competitions.api.filters import competition_filters, series_filters
from apps.competitions.api.serializers import (
    CompetitionDetailSerializer,
    CompetitionSerializer,
    SeriesSerializer,
)
from apps.competitions.selectors import get_competition, get_series, list_competitions, list_series
from apps.core.api import EnvelopeMixin
from apps.core.cache.policy import CachePlan, for_reference
from apps.core.cache_views import CachedReadMixin
from apps.core.pagination import CursorPagination, PageNumberPagination
from apps.matches.selectors.reads import (
    list_all_matches,
    match_summaries,
    series_squads,
    series_venues,
)


class _ReferenceDetailMixin(CachedReadMixin):
    """Detail views under a per-entity version namespace."""

    def cache_parts(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return (kwargs["pk"],)

    def cache_version_ns(self, request: Any, *args: Any, **kwargs: Any) -> str:
        return f"{self._entity}:{kwargs['pk']}"

    def cache_surrogates(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return (f"{self._entity}-{kwargs['pk']}",)

    _entity: str = ""


class CompetitionListView(CachedReadMixin, ListAPIView):  # type: ignore[type-arg]
    serializer_class = CompetitionSerializer
    pagination_class = PageNumberPagination
    cache_namespace = "competitions"

    def cache_plan(self) -> Any:
        return for_reference()

    def get_queryset(self) -> Any:
        return list_competitions(**competition_filters(self.request))


class CompetitionRetrieveView(EnvelopeMixin, _ReferenceDetailMixin, RetrieveAPIView):  # type: ignore[type-arg]
    serializer_class = CompetitionDetailSerializer
    cache_namespace = "competition"
    _entity = "competition"

    def cache_plan(self) -> Any:
        return for_reference()

    def get_object(self) -> Any:
        return get_competition(self.kwargs["pk"])


class SeriesListView(CachedReadMixin, ListAPIView):  # type: ignore[type-arg]
    serializer_class = SeriesSerializer
    pagination_class = PageNumberPagination
    cache_namespace = "series"

    def cache_plan(self) -> Any:
        return for_reference()

    def get_queryset(self) -> Any:
        return list_series(**series_filters(self.request))


class SeriesRetrieveView(EnvelopeMixin, _ReferenceDetailMixin, RetrieveAPIView):  # type: ignore[type-arg]
    serializer_class = SeriesSerializer
    cache_namespace = "series:detail"
    _entity = "series"

    def cache_plan(self) -> Any:
        return CachePlan(redis_ttl=60, max_age=60)

    def get_object(self) -> Any:
        return get_series(self.kwargs["pk"])


class _SeriesSubResource(CachedReadMixin, APIView):
    """Caching identity shared by the ``/series/{id}/...`` sub-resources.

    All of them invalidate under the ``series:{pk}`` version namespace, so a
    series edit or a new match in it re-renders every sub-resource.
    """

    def cache_parts(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return (kwargs["pk"],)

    def cache_version_ns(self, request: Any, *args: Any, **kwargs: Any) -> str:
        return f"series:{kwargs['pk']}"

    def cache_surrogates(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return (f"series-{kwargs['pk']}",)

    def cache_plan(self) -> Any:
        return for_reference()


class SeriesMatchesView(_SeriesSubResource):
    """``GET /series/{id}/matches`` — the series' matches, cursor-paginated
    (Group 8 coverage audit; same shell items as the match list feeds)."""

    cache_namespace = "series:matches"

    def get(self, request: Any, *args: Any, **kwargs: Any) -> Response:
        get_series(kwargs["pk"])  # 404 for an unknown series
        queryset = list_all_matches(series_id=kwargs["pk"])
        paginator = CursorPagination()
        paginator.ordering = ("-start_time_utc",)
        rows = paginator.paginate_queryset(queryset, request)
        return Response(
            {
                "data": match_summaries(rows),
                "meta": {
                    "limit": paginator.page_size_value,
                    "has_more": paginator.has_more,
                    "next_cursor": paginator.next_cursor,
                },
            }
        )


class SeriesSquadsView(_SeriesSubResource):
    """``GET /series/{id}/squads`` — playing-XI union keyed by team name."""

    cache_namespace = "series:squads"

    def get(self, request: Any, *args: Any, **kwargs: Any) -> Response:
        get_series(kwargs["pk"])
        data = series_squads(kwargs["pk"])
        return Response({"data": data, "meta": {"count": sum(len(v) for v in data.values())}})


class SeriesVenuesView(_SeriesSubResource):
    """``GET /series/{id}/venues`` — distinct venues used by the series."""

    cache_namespace = "series:venues"

    def get(self, request: Any, *args: Any, **kwargs: Any) -> Response:
        get_series(kwargs["pk"])
        rows = series_venues(kwargs["pk"])
        return Response({"data": rows, "meta": {"count": len(rows)}})


class ArchivesView(CachedReadMixin, APIView):
    """``GET /archives`` — series by year/category, newest start first.

    ``?year=``/``?category=``/``?type=`` map onto the same validated filters as
    ``/series``; the default ordering is ``-start_date`` so recent seasons
    surface first.
    """

    cache_namespace = "archives"

    def cache_plan(self) -> Any:
        return for_reference()

    def get(self, request: Any) -> Response:
        filters = series_filters(request)
        if "sort" not in request.query_params:
            filters["ordering"] = "-start_date"
        queryset = list_series(**filters)
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(queryset, request, view=self) or []
        rows = [
            {
                "id": str(series.id),
                "name": series.name,
                "series_type": series.series_type,
                "is_tournament": series.is_tournament,
                "start_date": str(series.start_date) if series.start_date else None,
                "end_date": str(series.end_date) if series.end_date else None,
                "competition": (
                    {"id": str(series.competition_id), "name": series.competition.name}
                    if series.competition_id and series.competition
                    else None
                ),
            }
            for series in page
        ]
        return paginator.get_paginated_response(rows)


__all__ = [
    "ArchivesView",
    "CompetitionListView",
    "CompetitionRetrieveView",
    "SeriesListView",
    "SeriesMatchesView",
    "SeriesRetrieveView",
    "SeriesSquadsView",
    "SeriesVenuesView",
]
