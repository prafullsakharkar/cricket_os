"""Player API routes (Group 7)."""

from __future__ import annotations

from django.urls import path

from apps.players.api.views import PlayerListView, PlayerRetrieveView

app_name = "players"

urlpatterns = [
    path("players", PlayerListView.as_view(), name="player-list"),
    path("players/<uuid:pk>/", PlayerRetrieveView.as_view(), name="player-detail"),
]
