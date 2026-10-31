"""Team API routes (Group 7)."""

from __future__ import annotations

from django.urls import path

from apps.teams.api.views import TeamListView, TeamRetrieveView

app_name = "teams"

urlpatterns = [
    path("teams", TeamListView.as_view(), name="team-list"),
    path("teams/<uuid:pk>/", TeamRetrieveView.as_view(), name="team-detail"),
]
