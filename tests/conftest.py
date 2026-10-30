"""Shared pytest fixtures.

Determinism rules (TESTING_STRATEGY §1): freeze time, generate UUIDv7 with a
seeded clock, no live network. Redis is only touched by fixtures that ask for it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from django.core.cache import cache

FROZEN_INSTANT = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def api_client() -> object:
    """A DRF test client for hitting `/api/v1/`."""
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def frozen_clock() -> Iterator[datetime]:
    """Freeze the application clock at a fixed instant."""
    from apps.core import time as core_time

    with core_time.frozen(FROZEN_INSTANT):
        yield FROZEN_INSTANT


@pytest.fixture
def seeded_uuid7_clock() -> Iterator[object]:
    """Install a deterministic, stepping UUIDv7 clock for the test."""
    from apps.core import uuid7 as uuid7_module

    class SteppingClock:
        def __init__(self, start_ms: int = 1_700_000_000_000) -> None:
            self._ms = start_ms

        def __call__(self) -> int:
            self._ms += 1
            return self._ms

    clock = SteppingClock()
    with uuid7_module.use_clock(clock):
        yield clock


@pytest.fixture
def redis_client() -> object:
    """A live Redis client, or skip if Redis is not reachable."""
    from django_redis import get_redis_connection

    try:
        connection = get_redis_connection("default")
        connection.ping()
    except Exception as exc:
        pytest.skip(f"Redis unavailable: {exc}")
    return connection


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    """Keep the in-memory cache isolated between tests.

    No-op when Django is not configured (e.g. the pure scoring suite under
    mutmut, which runs without Django settings): ``cache.clear()`` would force
    a settings lookup that cannot succeed there.
    """
    from django.conf import settings

    if not settings.configured:
        yield
        return
    cache.clear()
    yield
    cache.clear()
