"""Match read API routes (CRICKET_API_SPECIFICATION §3.2–§3.4)."""

from __future__ import annotations

from django.urls import path

from apps.matches.api.views import (
    DeliveriesView,
    InningsView,
    MatchDetailView,
    ScorecardView,
)

app_name = "matches"

urlpatterns = [
    path("matches/<uuid:pk>/", MatchDetailView.as_view(), name="match-detail"),
    path(
        "matches/<uuid:pk>/scorecard",
        ScorecardView.as_view(),
        name="match-scorecard",
    ),
    path("matches/<uuid:pk>/innings", InningsView.as_view(), name="match-innings"),
    path(
        "matches/<uuid:pk>/innings/<int:innings_number>/deliveries",
        DeliveriesView.as_view(),
        name="match-deliveries",
    ),
]
