"""Fixture-window orchestration for the live ingestion surface.

The synchronization module owns the idempotent ``sync_fixtures`` upsert; this
module is the *caller* side: it resolves the provider's adapter, computes the
window, normalizes the adapter's raw fixtures into ``FixtureRecord`` objects
and wires the error reporter to the ``IngestionError`` DLQ (the dependency
inversion established for reconciliation — ``apps.live`` may import
``apps.ingestion``, ``apps.synchronization`` may not).

``run_fixture_sync`` is a no-op report for providers without an adapter or for
providers that are inactive, and is safe to call on every beat tick.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.providers.base import DateRange
from apps.providers.models import DataProvider
from apps.providers.registry import ProviderNotRegistered, get_adapter
from apps.synchronization.services.fixtures import FixtureRecord, FixtureSyncReport, sync_fixtures

#: Default fixture window (days ahead) when not overridden.
DEFAULT_FIXTURE_WINDOW_DAYS = 7


def _dlq_report_fixture_error(record: FixtureRecord, exc: Exception) -> None:
    """Redacted DLQ row for one rejected fixture (never raises)."""
    from apps.ingestion.services.jobs import record_error

    record_error(
        None,
        stage="fixture_sync",
        payload={
            "provider_match_id": record.provider_match_id,
            "error": str(exc),
        },
        error_class=exc.__class__.__name__,
        error_detail=f"fixture {record.provider_match_id!r} rejected",
    )


def normalize_fixture(raw: Any, *, provider: Any = None) -> FixtureRecord:
    """Coerce an adapter fixture into the sync ``FixtureRecord`` shape.

    Accepts a ``FixtureRecord`` (pass-through), a mapping (dataclass kwargs)
    or any object with the same attributes (an adapter's raw fixture whose
    ``normalize`` was already applied). ``provider`` is accepted for
    signature symmetry with the adapter protocol.
    """
    if isinstance(raw, FixtureRecord):
        return raw
    if isinstance(raw, dict):
        return FixtureRecord(**raw)
    kwargs = {
        name: getattr(raw, name)
        for name in FixtureRecord.__dataclass_fields__
        if hasattr(raw, name)
    }
    return FixtureRecord(**kwargs)


def run_fixture_sync(
    provider: DataProvider,
    *,
    days: int = DEFAULT_FIXTURE_WINDOW_DAYS,
    on_error: Any = None,
) -> FixtureSyncReport:
    """Sync the next ``days`` of fixtures for ``provider`` (idempotent).

    Returns an empty report (provider not found / inactive / no adapter) so
    the beat task can treat every tick uniformly.
    """
    if not provider.is_active:
        return FixtureSyncReport(provider_code=provider.code)
    try:
        adapter = get_adapter(provider.code)
    except ProviderNotRegistered:
        return FixtureSyncReport(provider_code=provider.code)

    today = timezone.localdate()
    window = DateRange(start=today, end=today + timedelta(days=days))
    raw_fixtures = adapter.list_fixtures(window)
    records = [normalize_fixture(raw, provider=provider) for raw in raw_fixtures]
    return sync_fixtures(
        provider,
        records,
        source=provider,
        on_error=on_error if on_error is not None else _dlq_report_fixture_error,
    )


def fixture_sync_enabled() -> bool:
    """True when live ingestion is enabled (the beat task gate)."""
    return bool(getattr(settings, "LIVE_INGESTION_ENABLED", False))


__all__ = [
    "DEFAULT_FIXTURE_WINDOW_DAYS",
    "fixture_sync_enabled",
    "normalize_fixture",
    "run_fixture_sync",
]
