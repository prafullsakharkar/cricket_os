"""Health endpoint: ok path, dependency failures, caching header."""

from __future__ import annotations

from unittest import mock

import pytest

pytestmark = pytest.mark.integration

HEALTH = "/api/v1/health"


def test_health_ok_returns_documented_body(api_client: object) -> None:
    with (
        mock.patch("apps.core.health.check_database"),
        mock.patch("apps.core.health.check_redis"),
    ):
        response = api_client.get(HEALTH)  # type: ignore[attr-defined]
    assert response.status_code == 200
    assert response.json() == {
        "data": {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}
    }
    assert response["Cache-Control"] == "no-cache"


def test_health_uses_the_real_database(api_client: object, db) -> None:
    with mock.patch("apps.core.health.check_redis"):
        response = api_client.get(HEALTH)  # type: ignore[attr-defined]
    assert response.status_code == 200
    assert response.json()["data"]["checks"]["database"] == "ok"


def test_health_reports_database_failure_as_503(api_client: object) -> None:
    with (
        mock.patch("apps.core.health.check_database", side_effect=RuntimeError("db down")),
        mock.patch("apps.core.health.check_redis"),
    ):
        response = api_client.get(HEALTH)  # type: ignore[attr-defined]
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "upstream_unavailable"
    assert body["error"]["details"]["checks"]["database"] == "error"
    assert response["Cache-Control"] == "no-cache"


def test_health_reports_redis_failure_as_503(api_client: object) -> None:
    with (
        mock.patch("apps.core.health.check_database"),
        mock.patch("apps.core.health.check_redis", side_effect=RuntimeError("redis down")),
    ):
        response = api_client.get(HEALTH)  # type: ignore[attr-defined]
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "upstream_unavailable"
    assert body["error"]["details"]["checks"]["redis"] == "error"


def test_health_needs_no_authentication(api_client: object) -> None:
    with (
        mock.patch("apps.core.health.check_database"),
        mock.patch("apps.core.health.check_redis"),
    ):
        response = api_client.get(HEALTH)  # type: ignore[attr-defined]
    assert response.status_code == 200
