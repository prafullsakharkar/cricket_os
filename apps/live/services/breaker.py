"""Per-provider-endpoint circuit breaker (DATA_INGESTION §4.4, SECURITY §8).

State machine:

* ``CLOSED`` — calls flow. ``failure_threshold`` consecutive (or cumulative)
  failures open the breaker;
* ``OPEN`` — calls are rejected with :class:`CircuitOpenError`. After
  ``recovery_timeout`` seconds (virtual clock) the breaker moves to
  ``HALF_OPEN`` and lets a bounded probe through;
* ``HALF_OPEN`` — one probe call is allowed; a success counts toward
  ``success_threshold`` (consecutive) and closes the breaker, a failure reopens
  it immediately.

The clock is injected (``now: Callable[[], float]``) so the state machine is
unit-testable with a virtual clock and no sleeping. The state is exposed as the
``circuit_breaker_state{provider, endpoint}`` gauge (0=closed, 1=half-open,
2=open) for the alert rules. A provider failure must never block API reads:
readers observe the breaker via :func:`state` and serve last-known-good with
``meta.stale=true`` instead of calling the provider.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from apps.core import metrics

STATE_CLOSED = 0
STATE_HALF_OPEN = 1
STATE_OPEN = 2


class CircuitState(StrEnum):
    CLOSED = "closed"
    HALF_OPEN = "half_open"
    OPEN = "open"


class CircuitOpenError(Exception):
    """The breaker is OPEN; callers must serve last-known-good, not retry."""


@dataclass(frozen=True)
class BreakerConfig:
    """Thresholds for one breaker."""

    failure_threshold: int = 5
    success_threshold: int = 2
    recovery_timeout: float = 60.0
    max_probes_half_open: int = 1


class CircuitBreaker:
    """Thread-safe circuit breaker with an injectable clock."""

    def __init__(
        self,
        name: str,
        *,
        config: BreakerConfig | None = None,
        now: Callable[[], float] | None = None,
        provider: str = "",
        endpoint: str = "",
    ) -> None:
        self.name = name
        self.config = config or BreakerConfig()
        self._now = now or _monotonic
        self._provider = provider
        self._endpoint = endpoint
        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open_successes = 0
        self._half_open_calls = 0
        self._sync_metric()

    # -- state ------------------------------------------------------------ #
    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN and self._ready_for_probe():
                self._transition(CircuitState.HALF_OPEN)
            return self._state

    def state_code(self) -> int:
        return {
            CircuitState.CLOSED: STATE_CLOSED,
            CircuitState.HALF_OPEN: STATE_HALF_OPEN,
            CircuitState.OPEN: STATE_OPEN,
        }[self.state]

    def _ready_for_probe(self) -> bool:
        return (
            self._opened_at is not None
            and (self._now() - self._opened_at) >= self.config.recovery_timeout
        )

    # -- call flow -------------------------------------------------------- #
    def allow(self) -> bool:
        """True when a call may proceed; ``False`` means the breaker is open."""
        if self.state != CircuitState.OPEN:
            return True
        self._raise_open()
        return False  # pragma: no cover - _raise_open always raises

    def __call__(self) -> bool:
        return self.allow()

    def record_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._half_open_successes += 1
                if self._half_open_successes >= self.config.success_threshold:
                    self._transition(CircuitState.CLOSED)
            elif self._state == CircuitState.CLOSED:
                self._failures = 0

    def record_failure(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._transition(CircuitState.OPEN)
                return
            self._failures += 1
            if self._failures >= self.config.failure_threshold:
                self._transition(CircuitState.OPEN)

    def _transition(self, new_state: CircuitState) -> None:
        self._state = new_state
        if new_state == CircuitState.OPEN:
            self._opened_at = self._now()
            self._half_open_successes = 0
            self._half_open_calls = 0
        elif new_state == CircuitState.CLOSED:
            self._failures = 0
            self._opened_at = None
            self._half_open_successes = 0
            self._half_open_calls = 0
        elif new_state == CircuitState.HALF_OPEN:
            self._half_open_successes = 0
            self._half_open_calls = 0
        self._sync_metric()

    def _sync_metric(self) -> None:
        code = {
            CircuitState.CLOSED: STATE_CLOSED,
            CircuitState.HALF_OPEN: STATE_HALF_OPEN,
            CircuitState.OPEN: STATE_OPEN,
        }[self._state]
        metrics.BREAKER_STATE.labels(provider=self._provider, endpoint=self._endpoint).set(code)

    def _raise_open(self) -> None:
        raise CircuitOpenError(f"circuit breaker {self.name!r} is open (provider {self._provider})")

    def reset(self) -> None:
        """Force the breaker closed (admin action / manual recovery)."""
        with self._lock:
            self._transition(CircuitState.CLOSED)


def _monotonic() -> float:
    import time

    return time.monotonic()


__all__ = [
    "STATE_CLOSED",
    "STATE_HALF_OPEN",
    "STATE_OPEN",
    "BreakerConfig",
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitState",
]
