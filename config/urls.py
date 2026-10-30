"""Root URL configuration. All API routes live under ``/api/v1/``."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns: list[Any] = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("apps.core.urls")),
]

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
