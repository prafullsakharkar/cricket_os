"""Shared pytest fixtures.

Determinism rules (TESTING_STRATEGY §1): freeze time, generate UUIDv7 with a
seeded clock, no live network. Redis is only touched by fixtures that ask for it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

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


@pytest.fixture(scope="session", autouse=True)
def _flush_includes_unmanaged_app_tables() -> Iterator[None]:
    """Make Django's ``flush`` include our unmanaged tables.

    ``cricket.delivery`` (partitioned) is unmanaged, so Django's ``flush``
    omits it — but it has FKs INTO ``player``/``match``/``innings``, so a
    plain ``TRUNCATE`` without ``CASCADE`` (what TransactionTestCase teardown
    issues) fails on those tables. Patching the flush command's ``sql_flush``
    to append the unmanaged app tables to the TRUNCATE list makes every
    ``django_db(transaction=True)`` test teardown work. No-op when Django is
    not configured (pure scoring suite under mutmut).
    """
    from django.conf import settings

    if not settings.configured:
        yield
        return
    import django.core.management.commands.flush as flush_command
    from django.apps import apps

    original = flush_command.sql_flush

    def patched(style: object, connection: object, **kwargs: object) -> list[str]:
        tables = set(connection.introspection.django_table_names(only_existing=True))  # type: ignore[attr-defined]
        for app_config in apps.get_app_configs():
            for model in app_config.get_models():
                if not model._meta.managed and model._meta.db_table:
                    tables.add(model._meta.db_table)
        return connection.ops.sql_flush(  # type: ignore[attr-defined]
            style,
            sorted(tables),
            reset_sequences=kwargs.get("reset_sequences", True),
            allow_cascade=kwargs.get("allow_cascade", False),
        )

    flush_command.sql_flush = patched
    try:
        yield
    finally:
        flush_command.sql_flush = original


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    """Keep the in-memory cache aliases isolated between tests.

    No-op when Django is not configured (e.g. the pure scoring suite under
    mutmut, which runs without Django settings): clearing would force a
    settings lookup that cannot succeed there.
    """
    from django.conf import settings

    if not settings.configured:
        yield
        return
    from django.core.cache import caches

    def clear_all() -> None:
        for alias in ("default", "cache"):
            try:
                caches[alias].clear()
            except Exception:  # alias absent in a specialised settings module
                continue

    clear_all()
    yield
    clear_all()


@pytest.fixture(autouse=True)
def _isolate_event_subscriptions() -> Iterator[None]:
    """Keep the event bus isolated between tests (Group 4, Task 4.3).

    The bus is a module-global registry populated by ``AppConfig.ready()``.
    Ad-hoc handlers a test subscribes must not leak into the next test, and
    the production audit handlers must be present for tests that exercise
    them. No-op when Django is not configured (pure scoring suite under
    mutmut).
    """
    from django.conf import settings

    if not settings.configured:
        yield
        return
    from apps.administration import handlers
    from apps.core import events

    events.clear_subscriptions()
    events.subscribe(events.TOPIC_PLAYER_MERGED, handlers.on_player_merged)
    events.subscribe(events.TOPIC_PROVIDER_MAP_VERIFIED, handlers.on_provider_map_verified)
    events.subscribe(events.TOPIC_DUPLICATE_REVIEWED, handlers.on_duplicate_reviewed)
    events.subscribe(events.TOPIC_MATCH_REOPENED, handlers.on_match_reopened)
    events.subscribe(events.TOPIC_CORRECTION_REQUESTED, handlers.on_correction_requested)
    # Phase E: cache invalidation subscribers live in core and are cleared by
    # ``clear_subscriptions`` above, so re-attach them for every test, and
    # reset the CDN purge hook (tests configure their own fake when needed).
    from apps.core.cache import invalidation, purger

    invalidation.resubscribe()
    invalidation.configure_purger(purger.NoopCachePurger())
    # Fresh rate-limit buckets per test (the in-process test backend must not
    # accumulate across tests sharing a client IP), and a clean metric slate so
    # counter assertions (throttling, realtime) start from zero.
    from apps.core import metrics
    from apps.core.throttling import reset_limiter

    reset_limiter()
    metrics.reset()
    yield
    events.clear_subscriptions()
    invalidation.configure_purger(purger.NoopCachePurger())
    reset_limiter()
    metrics.reset()
