"""UTC time helpers and a freezeable application clock.

Every API timestamp is rendered as ISO-8601 UTC with a trailing ``Z``.
Tests freeze the clock via :func:`frozen` (see ``tests/conftest.py``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

Clock = Callable[[], datetime]

_clock_var: ContextVar[Clock | None] = ContextVar("app_clock", default=None)


def utcnow() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(UTC)


def now() -> datetime:
    """Return the current time, honouring a frozen clock if one is installed."""
    clock = _clock_var.get()
    return clock() if clock is not None else utcnow()


@contextmanager
def frozen(instant: datetime) -> Iterator[Clock]:
    """Temporarily pin :func:`now` to ``instant`` for the duration of the block."""
    clock: Clock = lambda: instant  # noqa: E731 - trivial frozen clock
    token = _clock_var.set(clock)
    try:
        yield clock
    finally:
        _clock_var.reset(token)


def to_iso_z(value: datetime) -> str:
    """Format an aware datetime as ISO-8601 UTC with a ``Z`` suffix."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("to_iso_z() requires a timezone-aware datetime")
    utc = value.astimezone(UTC)
    if utc.microsecond == 0:
        return utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    return utc.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
