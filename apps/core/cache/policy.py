"""State-dependent cache policy (API_SPECIFICATION §3.9, PERFORMANCE §5).

One :class:`CachePlan` drives both the Redis TTL and the HTTP ``Cache-Control``
header so they can never drift apart:

* **live** — seconds-level freshness;
* **completed** — long browser TTL plus a longer CDN TTL and
  ``stale-if-error``;
* **immutable** — once the post-completion reconciliation window has passed and
  corrections can no longer arrive, the payload is frozen for a year.

``stale-while-revalidate`` is expressed as seconds and is also used by
:mod:`apps.core.cache.store` as the soft/hard expiry gap.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

#: Lifecycle states after which a match is frozen except for corrections.
TERMINAL_STATUSES: frozenset[str] = frozenset({"COMPLETED", "ABANDONED", "CANCELLED", "FORFEITED"})


@dataclass(frozen=True)
class CachePlan:
    """Redis TTL + HTTP caching directives for one resource class."""

    redis_ttl: int
    max_age: int
    swr: int | None = None
    s_maxage: int | None = None
    stale_if_error: int | None = None
    immutable: bool = False

    def cache_control(self) -> str:
        """Render the ``Cache-Control`` header value."""
        directives = ["public", f"max-age={self.max_age}"]
        if self.s_maxage is not None:
            directives.append(f"s-maxage={self.s_maxage}")
        if self.swr is not None:
            directives.append(f"stale-while-revalidate={self.swr}")
        if self.stale_if_error is not None:
            directives.append(f"stale-if-error={self.stale_if_error}")
        if self.immutable:
            directives.append("immutable")
        return ", ".join(directives)


def for_live() -> CachePlan:
    """Live match sub-resources and the live feed (seconds)."""
    return CachePlan(redis_ttl=5, max_age=5, swr=5)


def for_completed() -> CachePlan:
    """Completed match content inside the reconciliation window."""
    return CachePlan(
        redis_ttl=900,
        max_age=300,
        swr=600,
        s_maxage=3600,
        stale_if_error=86400,
    )


def for_immutable() -> CachePlan:
    """Content past the reconciliation window: freeze it at the CDN."""
    return CachePlan(
        redis_ttl=86400,
        max_age=31536000,
        s_maxage=31536000,
        swr=None,
        immutable=True,
    )


def for_fixtures() -> CachePlan:
    """Schedules/upcoming feeds."""
    return CachePlan(redis_ttl=60, max_age=30, swr=60)


def for_reference() -> CachePlan:
    """Teams, venues, competitions: reference data changes rarely."""
    return CachePlan(redis_ttl=3600, max_age=600, swr=600)


def for_player() -> CachePlan:
    """Player profiles (stable fields, corrections possible)."""
    return CachePlan(redis_ttl=300, max_age=300, swr=600)


def reconciliation_window_seconds() -> int:
    """The post-completion window during which corrections may still land."""
    hours = getattr(settings, "LIVE_RECONCILIATION_WINDOW_HOURS", 24)
    return int(hours) * 3600


def for_match(
    status: str,
    *,
    ended_at: float | None = None,
    now: float,
) -> CachePlan:
    """Pick a plan from the match lifecycle and its end time.

    A match that is not terminal is always live, even if a stale ``end_time``
    is present (a reopened match must go back to seconds-level freshness).
    """
    normalized = str(status).upper()
    if normalized not in TERMINAL_STATUSES:
        return for_live()
    if ended_at is None:
        return for_completed()
    if now - ended_at >= reconciliation_window_seconds():
        return for_immutable()
    return for_completed()


__all__ = [
    "TERMINAL_STATUSES",
    "CachePlan",
    "for_completed",
    "for_fixtures",
    "for_immutable",
    "for_live",
    "for_match",
    "for_player",
    "for_reference",
    "reconciliation_window_seconds",
]
