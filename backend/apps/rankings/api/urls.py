"""Rankings API routes (CRICKET_API_SPECIFICATION §3.7)."""

from __future__ import annotations

from django.urls import path

from apps.rankings.api.views import PlayerRankingsView, RankingsListView

app_name = "rankings"

urlpatterns = [
    path("rankings", RankingsListView.as_view(), name="ranking-list"),
    path(
        "players/<uuid:pk>/rankings",
        PlayerRankingsView.as_view(),
        name="player-ranking-history",
    ),
]
