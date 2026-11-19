"""URL routes under ``/api/v1/`` contributed by the core app."""

from __future__ import annotations

from django.urls import path

from apps.core.views import HealthView

app_name = "core"

urlpatterns = [
    path("health", HealthView.as_view(), name="health"),
]
