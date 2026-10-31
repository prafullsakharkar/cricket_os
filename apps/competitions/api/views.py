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
from apps.core.http import CacheControlMixin
from apps.core.pagination import PageNumberPagination


class CompetitionListView(CacheControlMixin, ListAPIView):  # type: ignore[type-arg]
    serializer_class = CompetitionSerializer
    pagination_class = PageNumberPagination
    cache_max_age = 600

    def get_queryset(self) -> Any:
        return list_competitions(**competition_filters(self.request))


class CompetitionRetrieveView(EnvelopeMixin, CacheControlMixin, RetrieveAPIView):  # type: ignore[type-arg]
    serializer_class = CompetitionDetailSerializer
    cache_max_age = 600

    def get_object(self) -> Any:
        return get_competition(self.kwargs["pk"])


class SeriesListView(CacheControlMixin, ListAPIView):  # type: ignore[type-arg]
    serializer_class = SeriesSerializer
    pagination_class = PageNumberPagination
    cache_max_age = 600

    def get_queryset(self) -> Any:
        return list_series(**series_filters(self.request))


class SeriesRetrieveView(EnvelopeMixin, CacheControlMixin, RetrieveAPIView):  # type: ignore[type-arg]
    serializer_class = SeriesSerializer
    cache_max_age = 60

    def get_object(self) -> Any:
        return get_series(self.kwargs["pk"])


__all__ = [
    "CompetitionListView",
    "CompetitionRetrieveView",
    "SeriesListView",
    "SeriesRetrieveView",
]
