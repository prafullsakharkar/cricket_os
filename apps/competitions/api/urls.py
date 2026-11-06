"""Competition / Series API routes (Group 7)."""

from __future__ import annotations

from django.urls import path

from apps.competitions.api.views import (
    ArchivesView,
    CompetitionListView,
    CompetitionRetrieveView,
    SeriesListView,
    SeriesMatchesView,
    SeriesRetrieveView,
    SeriesSquadsView,
    SeriesVenuesView,
)

app_name = "competitions"

urlpatterns = [
    path("archives", ArchivesView.as_view(), name="archives"),
    path("competitions", CompetitionListView.as_view(), name="competition-list"),
    path("competitions/<uuid:pk>/", CompetitionRetrieveView.as_view(), name="competition-detail"),
    path("series", SeriesListView.as_view(), name="series-list"),
    path("series/<uuid:pk>/", SeriesRetrieveView.as_view(), name="series-detail"),
    path("series/<uuid:pk>/matches", SeriesMatchesView.as_view(), name="series-matches"),
    path("series/<uuid:pk>/squads", SeriesSquadsView.as_view(), name="series-squads"),
    path("series/<uuid:pk>/venues", SeriesVenuesView.as_view(), name="series-venues"),
]
