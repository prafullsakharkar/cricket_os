"""Team list/detail views (trigram search via the selector)."""

from __future__ import annotations

from typing import Any

from rest_framework.generics import ListAPIView, RetrieveAPIView

from apps.core.api import EnvelopeMixin
from apps.core.http import CacheControlMixin
from apps.core.pagination import PageNumberPagination
from apps.teams.api.filters import team_filters
from apps.teams.api.serializers import TeamSerializer
from apps.teams.selectors import get_team, list_teams


class TeamListView(CacheControlMixin, ListAPIView):  # type: ignore[type-arg]
    serializer_class = TeamSerializer
    pagination_class = PageNumberPagination
    cache_max_age = 600

    def get_queryset(self) -> Any:
        return list_teams(**team_filters(self.request))


class TeamRetrieveView(EnvelopeMixin, CacheControlMixin, RetrieveAPIView):  # type: ignore[type-arg]
    serializer_class = TeamSerializer
    cache_max_age = 600

    def get_object(self) -> Any:
        return get_team(self.kwargs["pk"])


__all__ = ["TeamListView", "TeamRetrieveView"]
