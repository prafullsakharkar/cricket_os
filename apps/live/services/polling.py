"""Live polling dispatcher (DATA_INGESTION §2/§4.4, ROADMAP Phase D).

A per-match poll cycle:

1. **TTL lock** (Redis via the cache backend) prevents overlapping polls for
   the same match. Correctness does NOT depend on this lock — the per-match
   Postgres advisory lock and idempotent writes do the real work; the TTL lock
   only de-duplicates polls. A crashed worker's lock expires by TTL.
2. **Cursor** — the poll resumes from ``SyncCheckpoint.last_delivery_seq`` for
   ``match:{id}`` (advanced only after commit by ``apply_batch``), so nothing
   is re-fetched and nothing is skipped after a crash.
3. **Fetch + apply** — the adapter's fetcher returns ``LiveInningsBatch``
   records; ``apply_batch`` writes them atomically.
4. **Breaker** — an open breaker skips the fetch entirely; the API keeps
   serving last-known-good with ``meta.stale=true``.

Adaptive cadence (:func:`cadence_for`) polls faster while play is in progress
and slower in breaks or before the toss.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.cache import cache as default_cache

from apps.core.enums import MatchStatus
from apps.live.services.breaker import CircuitBreaker, CircuitOpenError, register_breaker
from apps.matches.models import Match
from apps.synchronization.services.sync import get_checkpoint

#: Statuses a poll dispatcher cares about (matches that could change).
LIVE_STATUSES = frozenset(
    {
        MatchStatus.TOSS,
        MatchStatus.IN_PROGRESS,
        MatchStatus.INNINGS_BREAK,
        MatchStatus.DRINKS,
        MatchStatus.RAIN_DELAY,
        MatchStatus.BAD_LIGHT,
        MatchStatus.STUMPS,
        MatchStatus.DAY_BREAK,
    }
)

#: Statuses that slow polling down (play paused).
_SLOW_STATUSES = frozenset(
    {
        MatchStatus.TOSS,
        MatchStatus.INNINGS_BREAK,
        MatchStatus.DRINKS,
        MatchStatus.RAIN_DELAY,
        MatchStatus.BAD_LIGHT,
        MatchStatus.DAY_BREAK,
        MatchStatus.STUMPS,
    }
)

Lock = Any


def cadence_for(match: Match, *, base: int | None = None) -> int:
    """Adaptive poll cadence in seconds.

    IN_PROGRESS → ``base``; breaks/toss → ``2 * base``; scheduled/terminal →
    ``6 * base`` (they are picked up by fixture sync, not polling).
    """
    base = base if base is not None else int(getattr(settings, "LIVE_POLL_INTERVAL_SECONDS", 30))
    if match.status == MatchStatus.IN_PROGRESS:
        return base
    if match.status in _SLOW_STATUSES:
        return base * 2
    return base * 6


def acquire_poll_lock(match_id: Any, *, ttl: int | None = None) -> bool:
    """Atomic TTL lock; ``True`` when this worker won the poll slot."""
    ttl = ttl if ttl is not None else int(getattr(settings, "LIVE_POLL_LOCK_TTL_SECONDS", 300))
    return default_cache.add(f"live:poll:{match_id}", "1", timeout=ttl)


def release_poll_lock(match_id: Any) -> None:
    default_cache.delete(f"live:poll:{match_id}")


@dataclass(frozen=True)
class PollReport:
    """What one poll cycle did."""

    match_id: str
    polled: bool
    skipped_reason: str | None = None
    applied: int = 0
    corrected: int = 0
    noop: int = 0
    changed: bool = False
    stale: bool = False
    cadence_seconds: int = 0


def _register_breaker(provider: Any, breaker: CircuitBreaker) -> None:
    """Publish the breaker so readers can mark ``meta.stale`` (best-effort)."""
    if provider is None:
        return
    code = getattr(provider, "code", None) or str(provider)
    register_breaker(code, "poll", breaker)


def poll_live_match(
    match: Match,
    *,
    fetcher: Callable[[Match, int | None], Iterable[Any]],
    source: Any = None,
    provider: Any = None,
    breaker: CircuitBreaker | None = None,
) -> PollReport:
    """One poll cycle for ``match``; returns a report, never raises."""
    if breaker is not None:
        _register_breaker(provider, breaker)
    if not acquire_poll_lock(match.id):
        return PollReport(match_id=str(match.id), polled=False, skipped_reason="locked")

    try:
        if breaker is not None and breaker.state_code() == 2:  # OPEN
            return PollReport(
                match_id=str(match.id), polled=True, skipped_reason="breaker_open", stale=True
            )
        checkpoint = get_checkpoint(provider, f"match:{match.id}") if provider is not None else None
        since_seq = checkpoint.last_delivery_seq if checkpoint is not None else None

        from apps.live.services.apply import apply_batch

        apply_report = apply_batch(
            match,
            fetcher(match, since_seq),
            source=source,
            provider=provider,
            checkpoint_scope=f"match:{match.id}" if provider is not None else None,
        )
        if breaker is not None:
            breaker.record_success()
        return PollReport(
            match_id=str(match.id),
            polled=True,
            applied=apply_report.appended,
            corrected=apply_report.corrected,
            noop=apply_report.noop,
            changed=apply_report.changed,
            cadence_seconds=cadence_for(match),
        )
    except CircuitOpenError:
        return PollReport(
            match_id=str(match.id), polled=True, skipped_reason="breaker_open", stale=True
        )
    except Exception:
        if breaker is not None:
            breaker.record_failure()
        return PollReport(
            match_id=str(match.id), polled=True, skipped_reason="fetch_failed", stale=True
        )
    finally:
        release_poll_lock(match.id)


def live_matches() -> list[Match]:
    """Matches a dispatcher should poll right now (oldest first)."""
    return list(Match.objects.filter(status__in=LIVE_STATUSES).order_by("last_ingested_at"))


def dispatch_polls(
    *,
    provider: Any = None,
    fetcher_factory: Callable[[Match], Callable[[Match, int | None], Iterable[Any]]] | None = None,
    adapter: Any = None,
    breaker: CircuitBreaker | None = None,
    max_matches: int | None = None,
) -> list[PollReport]:
    """Poll every live match once (called by the beat task / command).

    ``fetcher_factory(match)`` returns a ``fetcher(match, since_seq)``; when
    an ``adapter`` is given instead, the default factory binds it to the
    match's ``provider_match_id``.
    """
    reports: list[PollReport] = []
    for match in live_matches()[: max_matches if max_matches is not None else None]:
        factory = fetcher_factory or _adapter_fetcher_factory(adapter)
        reports.append(
            poll_live_match(
                match,
                fetcher=factory(match),
                source=provider,
                provider=provider,
                breaker=breaker,
            )
        )
    return reports


def _adapter_fetcher_factory(
    adapter: Any,
) -> Callable[[Match], Callable[[Match, int | None], Iterable[Any]]]:
    """Bind an adapter's ``iter_deliveries`` to each match's provider id."""
    from apps.live.services.apply import LiveInningsBatch

    def factory(match: Match) -> Callable[[Match, int | None], Iterable[Any]]:
        def fetcher(match: Match, since_seq: int | None) -> Iterable[Any]:
            records = adapter.iter_deliveries(match.provider_match_id, since_seq)
            return [r for r in records if isinstance(r, LiveInningsBatch)]

        return fetcher

    return factory


__all__ = [
    "LIVE_STATUSES",
    "PollReport",
    "acquire_poll_lock",
    "cadence_for",
    "dispatch_polls",
    "live_matches",
    "poll_live_match",
    "release_poll_lock",
]
