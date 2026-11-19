"""Player list/detail views with dual pagination and merge 301 redirects.

``/players`` paginates with **cursor on (full_name, id)** when no ``q`` and
**page-number ranked by trigram similarity** when ``q`` is present
(CRICKET_API_SPECIFICATION §3.5). A merged player id answers ``301`` with a
``Location`` header pointing at its canonical player (no body, no cache
headers).
"""

from __future__ import annotations

from typing import Any

from django.http import HttpResponse
from rest_framework.generics import ListAPIView, RetrieveAPIView

from apps.core.api import EnvelopeMixin
from apps.core.cache.policy import CachePlan, for_player
from apps.core.cache_views import CachedReadMixin
from apps.core.pagination import CursorPagination, PageNumberPagination
from apps.players.api.filters import player_filters
from apps.players.api.serializers import PlayerSerializer
from apps.players.selectors import get_player, list_players, resolve_player_redirect


class _PlayerCursorPagination(CursorPagination):
    """Keyset pagination over (full_name, id) for the no-``q`` player list."""

    ordering = ("full_name",)


class PlayerListView(CachedReadMixin, ListAPIView):  # type: ignore[type-arg]
    serializer_class = PlayerSerializer
    cache_namespace = "players"

    def cache_plan(self) -> Any:
        return CachePlan(redis_ttl=300, max_age=300)

    def get_queryset(self) -> Any:
        return list_players(**player_filters(self.request))

    def paginate_queryset(self, queryset: Any) -> Any:
        # Dual pagination: cursor when browsing, page-number when ranking.
        if self.request.query_params.get("q"):
            self.pagination_class = PageNumberPagination
        else:
            self.pagination_class = _PlayerCursorPagination
        return super().paginate_queryset(queryset)


class PlayerRetrieveView(EnvelopeMixin, CachedReadMixin, RetrieveAPIView):  # type: ignore[type-arg]
    serializer_class = PlayerSerializer
    cache_namespace = "player"

    def cache_parts(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return (kwargs["pk"],)

    def cache_version_ns(self, request: Any, *args: Any, **kwargs: Any) -> str:
        return f"player:{kwargs['pk']}"

    def cache_surrogates(self, request: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        return (f"player-{kwargs['pk']}",)

    def cache_plan(self) -> Any:
        return for_player()

    def get(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        canonical = resolve_player_redirect(self.kwargs["pk"])
        if canonical is not None:
            # 301 to the canonical player; deliberately no cache headers.
            response = HttpResponse(status=301)
            response["Location"] = f"/api/v1/players/{canonical}/"
            return response
        return super().get(request, *args, **kwargs)

    def get_object(self) -> Any:
        return get_player(self.kwargs["pk"])


__all__ = ["PlayerListView", "PlayerRetrieveView"]
