"""UUIDv7 primary-key generator.

Time-ordered (leading 48-bit Unix millisecond timestamp) and **monotonic within
a process** thanks to a per-millisecond counter in the 12-bit ``rand_a`` field.
The clock is injectable so tests are deterministic (see ``tests/conftest.py``).
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

#: A clock returning the current Unix time in **milliseconds**.
MillisClock = Callable[[], int]

_MAX_RAND_A = 0x0FFF
_lock = threading.Lock()
_last_ms = -1
_counter = 0

_clock_var: ContextVar[MillisClock | None] = ContextVar("uuid7_clock", default=None)


def _default_clock() -> int:
    return time.time_ns() // 1_000_000


@contextmanager
def use_clock(clock: MillisClock) -> Iterator[MillisClock]:
    """Install a deterministic millisecond clock for the duration of the block.

    Entering (and leaving) the block resets the monotonic counter so each test
    gets an independent, reproducible timeline rather than inheriting the
    previous test's high-water mark.
    """
    global _last_ms, _counter

    token = _clock_var.set(clock)
    with _lock:
        _last_ms = -1
        _counter = 0
    try:
        yield clock
    finally:
        _clock_var.reset(token)
        with _lock:
            _last_ms = -1
            _counter = 0


def uuid7(clock: MillisClock | None = None) -> uuid.UUID:
    """Generate a UUIDv7, monotonic within this process.

    Args:
        clock: optional millisecond clock; overrides any installed clock.
    """
    global _last_ms, _counter

    source = clock or _clock_var.get() or _default_clock
    millis = int(source())

    with _lock:
        if millis > _last_ms:
            _last_ms = millis
            _counter = 0
        else:
            millis = _last_ms
            _counter += 1
            if _counter > _MAX_RAND_A:
                # Counter exhausted for this millisecond: borrow the next one.
                _last_ms += 1
                millis = _last_ms
                _counter = 0

        rand_a = _counter & _MAX_RAND_A
        rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
        value = (
            ((millis & ((1 << 48) - 1)) << 80)
            | (0x7 << 76)  # version 7
            | (rand_a << 64)
            | (0x2 << 62)  # RFC 4122 variant
            | rand_b
        )

    return uuid.UUID(int=value)
