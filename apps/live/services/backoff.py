"""Exponential backoff with jitter, honouring ``Retry-After`` (DATA_INGESTION §4.3).

``Retry-After`` from a ``429`` response wins when present (a provider that
tells you its rate-limit window should be believed). Otherwise the delay is
``min(base * 2**attempt, max_seconds)`` plus a random jitter of at most
``base`` seconds, bounded at :data:`MAX_BACKOFF_SECONDS` so a long outage never
schedules an absurd retry far in the future.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

#: Upper bound for any computed delay (seconds).
MAX_BACKOFF_SECONDS = 3600


@dataclass(frozen=True)
class BackoffPolicy:
    """Tuning for the exponential schedule."""

    base_seconds: int = 5
    max_seconds: int = MAX_BACKOFF_SECONDS
    jitter: bool = True


def compute_backoff(
    attempt: int,
    *,
    retry_after: int | None = None,
    policy: BackoffPolicy | None = None,
) -> int:
    """Seconds to wait before the next attempt.

    ``attempt`` is 0-based (the first retry has ``attempt == 0``).
    ``retry_after`` is an integer number of seconds taken verbatim from a
    provider ``429``/``Retry-After`` response and always wins when present.
    """
    if retry_after is not None:
        return max(0, int(retry_after))
    policy = policy or BackoffPolicy()
    base = min(policy.base_seconds * (2 ** max(0, attempt)), policy.max_seconds)
    if not policy.jitter:
        return base
    return base + random.randint(0, max(0, policy.base_seconds - 1))


__all__ = ["MAX_BACKOFF_SECONDS", "BackoffPolicy", "compute_backoff"]
