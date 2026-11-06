"""Phase F statistics/analytics API routes (CRICKET_API_SPECIFICATION §2).

Literal paths (``players/compare``, ``statistics/...``, ``records/...``,
``head-to-head``) are registered before any ``<uuid>`` route in the same file
so they are never shadowed; the player/team/series detail routes live in their
own apps and the UUID converters reject non-UUID path segments there.
"""

from __future__ import annotations

from django.urls import path

from apps.statistics.api import views

app_name = "statistics"

urlpatterns = [
    path("players/compare", views.PlayerCompareView.as_view(), name="player-compare"),
    path(
        "players/<uuid:pk>/statistics",
        views.PlayerStatisticsView.as_view(),
        name="player-statistics",
    ),
    path("players/<uuid:pk>/form", views.PlayerFormView.as_view(), name="player-form"),
    path(
        "teams/<uuid:pk>/statistics",
        views.TeamStatisticsView.as_view(),
        name="team-statistics",
    ),
    path(
        "matches/<uuid:pk>/statistics",
        views.MatchStatisticsView.as_view(),
        name="match-statistics",
    ),
    path(
        "series/<uuid:pk>/points-table",
        views.SeriesPointsTableView.as_view(),
        name="series-points-table",
    ),
    path("series/<uuid:pk>/stats", views.SeriesStatsView.as_view(), name="series-stats"),
    path("statistics/batting", views.BattingLeaderboardView.as_view(), name="leaderboard-batting"),
    path("statistics/bowling", views.BowlingLeaderboardView.as_view(), name="leaderboard-bowling"),
    path(
        "statistics/fielding",
        views.FieldingLeaderboardView.as_view(),
        name="leaderboard-fielding",
    ),
    path("statistics/teams", views.TeamRecordsView.as_view(), name="statistics-teams"),
    path("records/players", views.PlayerRecordsView.as_view(), name="records-players"),
    path("records/teams", views.TeamRecordsCatalogueView.as_view(), name="records-teams"),
    path("head-to-head", views.HeadToHeadView.as_view(), name="head-to-head"),
]
