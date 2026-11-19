"""Match read API routes (CRICKET_API_SPECIFICATION §3.2–§3.4)."""

from __future__ import annotations

from django.urls import path

from apps.matches.api.lists import (
    LiveMatchListView,
    MatchListView,
    RecentMatchListView,
    UpcomingMatchListView,
)
from apps.matches.api.views import (
    DeliveriesView,
    InningsView,
    MatchDetailView,
    MatchFactsView,
    MatchOversView,
    MatchSquadsView,
    ScorecardView,
)

app_name = "matches"

urlpatterns = [
    # List feeds first so the literal ``live``/``upcoming``/``recent`` paths
    # are never shadowed by the ``<uuid:pk>`` detail route.
    path("matches", MatchListView.as_view(), name="match-all"),
    path("matches/live", LiveMatchListView.as_view(), name="match-live"),
    path("matches/upcoming", UpcomingMatchListView.as_view(), name="match-upcoming"),
    path("matches/recent", RecentMatchListView.as_view(), name="match-recent"),
    path("matches/<uuid:pk>/", MatchDetailView.as_view(), name="match-detail"),
    path(
        "matches/<uuid:pk>/scorecard",
        ScorecardView.as_view(),
        name="match-scorecard",
    ),
    path("matches/<uuid:pk>/innings", InningsView.as_view(), name="match-innings"),
    path("matches/<uuid:pk>/overs", MatchOversView.as_view(), name="match-overs"),
    path("matches/<uuid:pk>/squads", MatchSquadsView.as_view(), name="match-squads"),
    path("matches/<uuid:pk>/facts", MatchFactsView.as_view(), name="match-facts"),
    path(
        "matches/<uuid:pk>/innings/<int:innings_number>/deliveries",
        DeliveriesView.as_view(),
        name="match-deliveries",
    ),
]
