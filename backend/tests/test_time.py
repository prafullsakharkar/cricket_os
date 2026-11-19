"""UTC time helpers and the freezeable clock abstraction."""

from __future__ import annotations

from datetime import UTC, datetime, timezone

import pytest

from apps.core.time import frozen, now, to_iso_z, utcnow

pytestmark = pytest.mark.unit


def test_to_iso_z_omits_empty_microseconds() -> None:
    value = datetime(2026, 10, 27, 8, 0, 0, tzinfo=UTC)
    assert to_iso_z(value) == "2026-10-27T08:00:00Z"


def test_to_iso_z_keeps_microseconds() -> None:
    value = datetime(2026, 10, 27, 8, 0, 0, 123456, tzinfo=UTC)
    assert to_iso_z(value) == "2026-10-27T08:00:00.123456Z"


def test_to_iso_z_converts_non_utc_offsets() -> None:
    value = datetime(
        2026, 10, 27, 10, 0, 0, tzinfo=timezone(__import__("datetime").timedelta(hours=2))
    )
    assert to_iso_z(value) == "2026-10-27T08:00:00Z"


def test_to_iso_z_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError):
        to_iso_z(datetime(2026, 10, 27, 8, 0, 0))


def test_utcnow_is_aware_and_utc() -> None:
    value = utcnow()
    assert value.tzinfo is UTC


def test_frozen_clock_overrides_now() -> None:
    instant = datetime(2026, 1, 1, 9, 30, tzinfo=UTC)
    with frozen(instant):
        assert now() == instant


def test_now_returns_after_frozen_exits() -> None:
    instant = datetime(2026, 1, 1, tzinfo=UTC)
    with frozen(instant):
        pass
    assert now() != instant
