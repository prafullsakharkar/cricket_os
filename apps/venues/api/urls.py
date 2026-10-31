"""Venue API routes (Group 7)."""

from __future__ import annotations

from django.urls import path

from apps.venues.api.views import VenueListView, VenueRetrieveView

app_name = "venues"

urlpatterns = [
    path("venues", VenueListView.as_view(), name="venue-list"),
    path("venues/<uuid:pk>/", VenueRetrieveView.as_view(), name="venue-detail"),
]
