"""Competition / Series list & detail views."""

from __future__ import annotations

from typing import Any

from rest_framework.generics import ListAPIView, RetrieveAPIView

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
from apps.core.pagination import PageNumberPagination


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


__all__ = [
    "CompetitionListView",
    "CompetitionRetrieveView",
    "SeriesListView",
    "SeriesRetrieveView",
]
