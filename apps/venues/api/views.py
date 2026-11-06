"""Venue list/detail views (bonus Group 7 endpoints; documented in the spec)."""

from __future__ import annotations

from typing import Any

from rest_framework.generics import ListAPIView, RetrieveAPIView

from apps.core.api import EnvelopeMixin
from apps.core.cache.policy import for_reference
from apps.core.cache_views import CachedReadMixin
from apps.core.pagination import PageNumberPagination
from apps.venues.api.serializers import VenueSerializer
from apps.venues.selectors import get_venue, list_venues


class VenueListView(CachedReadMixin, ListAPIView):  # type: ignore[type-arg]
    serializer_class = VenueSerializer
    pagination_class = PageNumberPagination
    cache_namespace = "venues"

    def cache_plan(self) -> Any:
        return for_reference()

    def get_queryset(self) -> Any:
        return list_venues()


class VenueRetrieveView(EnvelopeMixin, CachedReadMixin, RetrieveAPIView):  # type: ignore[type-arg]
    serializer_class = VenueSerializer
    cache_namespace = "venue"

    def cache_parts(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return (kwargs["pk"],)

    def cache_version_ns(self, request: Any, *args: Any, **kwargs: Any) -> str:
        return f"venue:{kwargs['pk']}"

    def cache_surrogates(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return (f"venue-{kwargs['pk']}",)

    def cache_plan(self) -> Any:
        return for_reference()

    def get_object(self) -> Any:
        return get_venue(self.kwargs["pk"])


__all__ = ["VenueListView", "VenueRetrieveView"]
