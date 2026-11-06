"""Search API routes (CRICKET_API_SPECIFICATION §2.6)."""

from __future__ import annotations

from django.urls import path

from apps.search.api.views import SearchView

app_name = "search"

urlpatterns = [
    path("search", SearchView.as_view(), name="search"),
]
