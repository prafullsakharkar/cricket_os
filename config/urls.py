"""Root URL configuration. All API routes live under ``/api/v1/``."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from apps.live.services.receiver import LivePushReceiverView

# URL-level errors keep the standard envelope (a non-UUID path id is a 404,
# never a 500).
handler404 = "apps.core.errors.handler404"
handler500 = "apps.core.errors.handler500"

urlpatterns: list[Any] = [
    path("admin/", admin.site.urls),
    # The authenticated live push receiver is an ingestion surface, deliberately
    # outside the public read-only API router (SECURITY §3, DATA_INGESTION §2).
    # The view 404s unless LIVE_PUSH_RECEIVER_ENABLED is on.
    path("ingest/live/push", LivePushReceiverView.as_view(), name="live-push-receiver"),
    path("api/v1/", include("apps.core.urls")),
    path("api/v1/", include("apps.venues.api.urls")),
    path("api/v1/", include("apps.teams.api.urls")),
    path("api/v1/", include("apps.players.api.urls")),
    path("api/v1/", include("apps.competitions.api.urls")),
    path("api/v1/", include("apps.matches.api.urls")),
    path("api/v1/", include("apps.rankings.api.urls")),
    path("api/v1/", include("apps.realtime.urls")),
]

# Internal Prometheus exposition — an ops surface, never public. Registered
# only when ENABLE_METRICS_ENDPOINT is on so the path 404s when disabled.
if getattr(settings, "ENABLE_METRICS_ENDPOINT", False):
    from apps.core.views import MetricsView

    urlpatterns.append(path("metrics", MetricsView.as_view(), name="metrics"))

# OpenAPI schema (drf-spectacular) feeds the contract tests; dev/test only.
if getattr(settings, "ENABLE_API_SCHEMA", False):
    from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

    urlpatterns += [
        path(
            "api/v1/schema",
            SpectacularAPIView.as_view(),
            name="api-schema",
        ),
        path(
            "api/v1/schema/swagger-ui/",
            SpectacularSwaggerView.as_view(url_name="api-schema"),
            name="api-schema-swagger-ui",
        ),
    ]
