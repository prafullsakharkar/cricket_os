"""drf-spectacular schema endpoint: reachable in dev/test, gated off in prod."""

from __future__ import annotations

import importlib
import json

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

pytestmark = pytest.mark.unit


def test_schema_endpoint_serves_openapi_document() -> None:
    client = APIClient()
    response = client.get("/api/v1/schema", HTTP_ACCEPT="application/json")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["openapi"].startswith("3.")
    assert body["info"]["title"] == "Cricket OS API"
    # Every documented path lives under /api/v1/
    assert all(path.startswith("/api/v1/") for path in body["paths"])


def test_schema_url_is_gated_by_enable_api_schema() -> None:
    """With the flag off the schema URL must not be registered at all."""

    def _has_schema_path(module: object) -> bool:
        return any(getattr(pattern, "name", None) == "api-schema" for pattern in module.urlpatterns)

    import config.urls as urls

    # Test settings enable the schema.
    assert _has_schema_path(urls)

    with override_settings(ENABLE_API_SCHEMA=False):
        reloaded = importlib.reload(urls)
        assert not _has_schema_path(reloaded)
    # Restore the enabled URLconf for the rest of the suite.
    importlib.reload(urls)
    assert _has_schema_path(urls)
