"""Phase E group 1: Redis logical roles and the role-aware client factory.

PERFORMANCE §5/§8 and the Phase E brief require separate logical roles
(cache / pubsub / ratelimit / Celery broker/result) with sane timeouts,
health checks and pooling, and a failure to reach Redis must never raise into
request handling.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import override_settings

from apps.core import redis as redis_roles

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_clients() -> Any:
    redis_roles.reset_clients()
    yield
    redis_roles.reset_clients()


def test_roles_are_explicit_and_distinct() -> None:
    assert redis_roles.ROLE_CACHE == "cache"
    assert redis_roles.ROLE_PUBSUB == "pubsub"
    assert redis_roles.ROLE_RATELIMIT == "ratelimit"
    assert redis_roles.ROLE_BROKER == "broker"
    assert redis_roles.ROLE_RESULT == "result"
    assert redis_roles.ROLES == (
        redis_roles.ROLE_CACHE,
        redis_roles.ROLE_PUBSUB,
        redis_roles.ROLE_RATELIMIT,
        redis_roles.ROLE_BROKER,
        redis_roles.ROLE_RESULT,
    )


def test_default_urls_use_separate_databases() -> None:
    urls = {role: redis_roles.role_url(role) for role in redis_roles.ROLES}
    # Cache keeps the historical default DB; every other role is separated.
    assert urls["cache"] != urls["pubsub"]
    assert urls["pubsub"] != urls["ratelimit"]
    assert urls["ratelimit"] != urls["broker"]
    assert urls["broker"] != urls["result"]


@override_settings(
    CACHE_REDIS_URL="redis://cache-host:6379/0",
    PUBSUB_REDIS_URL="redis://pubsub-host:6379/3",
    RATELIMIT_REDIS_URL="redis://rl-host:6379/4",
)
def test_role_url_overrides_from_settings() -> None:
    assert redis_roles.role_url(redis_roles.ROLE_CACHE) == "redis://cache-host:6379/0"
    assert redis_roles.role_url(redis_roles.ROLE_PUBSUB) == "redis://pubsub-host:6379/3"
    assert redis_roles.role_url(redis_roles.ROLE_RATELIMIT) == "redis://rl-host:6379/4"


def test_client_is_memoised_per_role(monkeypatch: Any) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_build(url: str, **kwargs: Any) -> object:
        calls.append((url, kwargs))
        return object()

    monkeypatch.setattr(redis_roles, "_build_client", fake_build)
    first = redis_roles.client(redis_roles.ROLE_CACHE)
    again = redis_roles.client(redis_roles.ROLE_CACHE)
    other = redis_roles.client(redis_roles.ROLE_PUBSUB)
    assert first is again
    assert first is not other
    assert len(calls) == 2


def test_client_configures_timeouts_health_checks_and_pooling(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_build(url: str, **kwargs: Any) -> object:
        captured.update(kwargs)
        captured["url"] = url
        return object()

    monkeypatch.setattr(redis_roles, "_build_client", fake_build)
    redis_roles.client(redis_roles.ROLE_PUBSUB)
    assert captured["socket_timeout"] == redis_roles.DEFAULT_SOCKET_TIMEOUT
    assert captured["socket_connect_timeout"] == redis_roles.DEFAULT_CONNECT_TIMEOUT
    assert captured["health_check_interval"] == redis_roles.DEFAULT_HEALTH_CHECK_INTERVAL
    assert captured["decode_responses"] is True
    assert "max_connections" in captured


def test_unhealthy_role_returns_false_and_never_raises(monkeypatch: Any) -> None:
    class Broken:
        def ping(self) -> None:
            raise ConnectionError("redis down")

    monkeypatch.setattr(redis_roles, "client", lambda role: Broken())
    assert redis_roles.is_healthy(redis_roles.ROLE_CACHE) is False


def test_healthy_role_returns_true(monkeypatch: Any) -> None:
    class Healthy:
        def ping(self) -> bool:
            return True

    monkeypatch.setattr(redis_roles, "client", lambda role: Healthy())
    assert redis_roles.is_healthy(redis_roles.ROLE_PUBSUB) is True


def test_safe_call_swallows_redis_errors() -> None:
    def boom() -> None:
        raise ConnectionError("down")

    assert redis_roles.safe_call(redis_roles.ROLE_CACHE, boom, default="fallback") == "fallback"
    assert redis_roles.safe_call(redis_roles.ROLE_CACHE, lambda: "ok") == "ok"


def test_unknown_role_is_rejected() -> None:
    with pytest.raises(ValueError):
        redis_roles.client("nope")
