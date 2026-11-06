"""Team list/detail views (trigram search via the selector)."""

from __future__ import annotations

from typing import Any

from rest_framework.generics import ListAPIView, RetrieveAPIView

from apps.core.api import EnvelopeMixin
from apps.core.cache.policy import for_reference
from apps.core.cache_views import CachedReadMixin
from apps.core.pagination import PageNumberPagination
from apps.teams.api.filters import team_filters
from apps.teams.api.serializers import TeamSerializer
from apps.teams.selectors import get_team, list_teams


class TeamListView(CachedReadMixin, ListAPIView):  # type: ignore[type-arg]
    serializer_class = TeamSerializer
    pagination_class = PageNumberPagination
    cache_namespace = "teams"

    def cache_plan(self) -> Any:
        return for_reference()

    def get_queryset(self) -> Any:
        return list_teams(**team_filters(self.request))


class TeamRetrieveView(EnvelopeMixin, CachedReadMixin, RetrieveAPIView):  # type: ignore[type-arg]
    serializer_class = TeamSerializer
    cache_namespace = "team"

    def cache_parts(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return (kwargs["pk"],)

    def cache_version_ns(self, request: Any, *args: Any, **kwargs: Any) -> str:
        return f"team:{kwargs['pk']}"

    def cache_surrogates(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return (f"team-{kwargs['pk']}",)

    def cache_plan(self) -> Any:
        return for_reference()

    def get_object(self) -> Any:
        return get_team(self.kwargs["pk"])


__all__ = ["TeamListView", "TeamRetrieveView"]
