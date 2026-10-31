"""Competition / Series API routes (Group 7)."""

from __future__ import annotations

from django.urls import path

from apps.competitions.api.views import (
    CompetitionListView,
    CompetitionRetrieveView,
    SeriesListView,
    SeriesRetrieveView,
)

app_name = "competitions"

urlpatterns = [
    path("competitions", CompetitionListView.as_view(), name="competition-list"),
    path("competitions/<uuid:pk>/", CompetitionRetrieveView.as_view(), name="competition-detail"),
    path("series", SeriesListView.as_view(), name="series-list"),
    path("series/<uuid:pk>/", SeriesRetrieveView.as_view(), name="series-detail"),
]
