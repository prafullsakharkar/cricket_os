"""UUIDv7 generator: version/variant, ordering, determinism (TESTING_STRATEGY §1)."""

from __future__ import annotations

import uuid

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from apps.core.uuid7 import use_clock, uuid7

pytestmark = pytest.mark.unit

FIXED_MS = 1_700_000_000_000


class SteppingClock:
    """A monotonic millisecond clock that advances on every call."""

    def __init__(self, start_ms: int = FIXED_MS) -> None:
        self._ms = start_ms

    def __call__(self) -> int:
        self._ms += 1
        return self._ms


def test_version_and_variant_bits_create_a_valid_v7() -> None:
    value = uuid7()
    assert value.version == 7
    assert value.variant == uuid.RFC_4122


def test_monotonic_within_one_millisecond() -> None:
    with use_clock(lambda: FIXED_MS):
        values = [uuid7() for _ in range(2_000)]
    assert values == sorted(values)
    assert len(set(values)) == len(values)


def test_timestamp_bits_are_deterministic_for_an_injected_clock() -> None:
    with use_clock(lambda: FIXED_MS):
        value = uuid7()
    assert (value.int >> 80) == FIXED_MS


def test_ordering_across_milliseconds() -> None:
    with use_clock(SteppingClock()):
        first = uuid7()
        second = uuid7()
    assert second > first


def test_explicit_clock_argument_overrides_the_installed_clock() -> None:
    with use_clock(lambda: FIXED_MS):
        value = uuid7(clock=lambda: FIXED_MS + 5)
    assert (value.int >> 80) == FIXED_MS + 5


@given(st.integers(min_value=1, max_value=5_000))
@settings(max_examples=25, deadline=None)
def test_sorted_uuids_preserve_generation_order(count: int) -> None:
    with use_clock(SteppingClock()):
        values = [uuid7() for _ in range(count)]
    assert values == sorted(values)
