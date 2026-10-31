"""Venue list/detail views (bonus Group 7 endpoints; documented in the spec)."""

from __future__ import annotations

from typing import Any

from rest_framework.generics import ListAPIView, RetrieveAPIView

from apps.core.api import EnvelopeMixin
from apps.core.http import CacheControlMixin
from apps.core.pagination import PageNumberPagination
from apps.venues.api.serializers import VenueSerializer
from apps.venues.selectors import get_venue, list_venues


class VenueListView(CacheControlMixin, ListAPIView):  # type: ignore[type-arg]
    serializer_class = VenueSerializer
    pagination_class = PageNumberPagination
    cache_max_age = 600

    def get_queryset(self) -> Any:
        return list_venues()


class VenueRetrieveView(EnvelopeMixin, CacheControlMixin, RetrieveAPIView):  # type: ignore[type-arg]
    serializer_class = VenueSerializer
    cache_max_age = 600

    def get_object(self) -> Any:
        return get_venue(self.kwargs["pk"])


__all__ = ["VenueListView", "VenueRetrieveView"]
