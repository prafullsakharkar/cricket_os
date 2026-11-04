"""ReplayAdapter — internal fake provider (DATA_INGESTION §2.1).

Replays a **recorded** (Phase C) match through the live pipeline as a fake
provider so Phases D/E can be built, exercised and load-tested before a
licensed live contract exists. It implements the same ``ProviderAdapter``
protocol, but its ``iter_deliveries``/``get_state`` return the normalized
``LiveInningsBatch``/``LiveState`` records the live write path consumes
directly (there is no provider-shaped payload to translate).

Design:

* **Deterministic virtual clock** — :class:`ReplayClock` advances explicitly
  (``advance``) so tests never sleep; a ``speed`` multiplier maps virtual time
  to real time for load runs. Pacing is opt-in (``paced=True``): unpaced, every
  available delivery is released immediately.
* **Fault injection** — :class:`ReplayFaults` covers the reliability matrix:
  duplicate, delayed, reordered, dropped, correction-after-completion,
  ``429`` Retry-After, ``5xx``, timeout, malformed payloads, an unknown status
  enum, an outage window and a per-call virtual delay. Faults are consumed
  deterministically so a test can script a scenario and assert on the result.
* **Corrections** are served on the poll *after* the recorded stream is fully
  consumed (``since_seq`` at the last delivery) with ``revision=2`` — exactly
  when the live pipeline would apply a provider revision.
* **Prod guard** — ``ReplayAdapter`` refuses to operate when
  ``settings.ENVIRONMENT == "prod"``: recorded data is internal and never
  redistributed, so the fake must not be constructible where it could be
  mistaken for a real feed.

The adapter reads recorded deliveries from the ``delivery`` table (the Phase C
data the match was built from); it never invents data.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Any

from django.conf import settings

from apps.core.errors import RateLimited, UpstreamUnavailable
from apps.providers.base import DateRange, ProviderCapabilities


class ReplayAdapterError(Exception):
    """Base for ReplayAdapter-specific failures."""


class ReplayDisabledInProduction(ReplayAdapterError):
    """The fake provider must never run in production."""


# --------------------------------------------------------------------------- #
# Deterministic virtual clock
# --------------------------------------------------------------------------- #


@dataclass
class ReplayClock:
    """Deterministic virtual clock: ``advance`` moves time, never ``sleep``.

    ``speed`` maps virtual seconds to real seconds: with ``speed=1`` one
    ``advance(2)`` moves the clock by 2 virtual seconds; with ``speed=10`` the
    same call moves it by 20 (a load run fast-forwards the replay).
    """

    epoch: float = 0.0
    speed: float = 1.0
    _t: float = field(default=0.0, init=False)

    def now(self) -> float:
        return self.epoch + self._t

    def advance(self, virtual_seconds: float) -> float:
        """Move the virtual clock by ``virtual_seconds`` (scaled by speed)."""
        self._t += virtual_seconds * self.speed
        return self.now()

    def __call__(self) -> float:
        return self.now()


# --------------------------------------------------------------------------- #
# Fault injection
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReplayFaults:
    """Fault-injection plan; every field optional, consumed deterministically.

    * ``duplicate`` — delivery seqs delivered twice (idempotency);
    * ``delayed`` — ``seq -> N``: hold that delivery for N adapter calls;
    * ``reordered`` — a permutation of delivery seqs applied to the first
      window (the stream is served in exactly that order);
    * ``dropped`` — delivery seqs never delivered (gap reconciliation);
    * ``correction`` — delivery seqs re-delivered with ``revision=2`` once the
      recorded stream is fully consumed (correction-after-completion);
    * ``retry_after`` — raise this many ``429`` (Retry-After) before success;
    * ``http_5xx`` — raise this many ``503`` before success;
    * ``timeout`` — raise this many timeouts before success;
    * ``malformed`` — delivery seqs served with an invalid dismissal enum;
    * ``unknown_status`` — ``get_state`` returns this unknown status value;
    * ``outage_calls`` — raise ``503`` for this many leading calls, then recover;
    * ``slow_seconds`` — virtual delay applied to the clock on every call.
    """

    duplicate: tuple[int, ...] = ()
    delayed: dict[int, int] = field(default_factory=dict)
    reordered: tuple[int, ...] = ()
    dropped: tuple[int, ...] = ()
    correction: tuple[int, ...] = ()
    retry_after: int = 0
    http_5xx: int = 0
    timeout: int = 0
    malformed: tuple[int, ...] = ()
    unknown_status: str | None = None
    outage_calls: int = 0
    slow_seconds: float = 0.0


# --------------------------------------------------------------------------- #
# ReplayAdapter
# --------------------------------------------------------------------------- #


class ReplayAdapter:
    """Replays recorded matches; fake provider for dev/test/load (never prod)."""

    def __init__(
        self,
        *,
        clock: ReplayClock | None = None,
        faults: ReplayFaults | None = None,
        paced: bool = False,
        release_interval: float = 30.0,
    ) -> None:
        if getattr(settings, "ENVIRONMENT", "dev") == "prod":
            raise ReplayDisabledInProduction(
                "ReplayAdapter is internal/test-only and cannot run in production"
            )
        self.clock = clock or ReplayClock()
        self.faults = faults or ReplayFaults()
        self.paced = paced
        self.release_interval = release_interval
        # Per-instance consumption state (deterministic per run).
        self._calls = 0
        self._held: dict[int, int] = dict(self.faults.delayed)
        self._dup_queue: list[tuple[int, int, Any]] = []
        self._corrections_served = False
        self._outage_budget = self.faults.outage_calls
        self._retry_budget = self.faults.retry_after
        self._5xx_budget = self.faults.http_5xx
        self._timeout_budget = self.faults.timeout

    # -- protocol ---------------------------------------------------------- #
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            ball_by_ball=True,
            commentary=False,
            historical_depth=True,
            push=False,
            features=("replay", "fault_injection"),
        )

    def list_fixtures(self, window: DateRange) -> Iterable[Any]:
        """Recorded matches whose start date falls inside ``window``."""
        from apps.matches.models import Match
        from apps.synchronization.services.fixtures import FixtureRecord

        matches = Match.objects.filter(start_time_utc__date__gte=window.start)
        if window.end is not None:
            matches = matches.filter(start_time_utc__date__lte=window.end)
        for match in matches.order_by("start_time_utc"):
            teams = tuple(
                mt.team.name for mt in match.teams.select_related("team").order_by("team_number")
            )
            yield FixtureRecord(
                provider_match_id=str(match.id),
                format_type=match.format_type,
                gender=match.gender,
                start_time_utc=match.start_time_utc,
                source_local_date=match.source_local_date,
                source_timezone=match.source_timezone or "UTC",
                overs_limit=match.overs_limit,
                balls_per_over=match.balls_per_over,
                status=match.status,
                teams=teams,
            )

    def get_match(self, provider_match_id: str) -> Any:
        """A normalized match shell (state + teams) for the recorded match."""
        match = self._resolve_match(provider_match_id)
        return {
            "match_id": str(match.id),
            "state": self._state_for(match),
            "teams": [
                {"name": mt.team.name, "team_number": mt.team_number}
                for mt in match.teams.select_related("team").order_by("team_number")
            ],
        }

    def get_state(self, provider_match_id: str) -> Any:
        """A ``LiveState`` for the recorded match (fault: unknown status)."""
        from apps.live.services.apply import LiveState

        match = self._resolve_match(provider_match_id)
        if self.faults.unknown_status:
            return LiveState(status=self.faults.unknown_status)
        return self._state_for(match)

    def get_scorecard(self, provider_match_id: str) -> Any:
        """The recorded innings summaries (provider card shape)."""
        match = self._resolve_match(provider_match_id)
        summaries = []
        for innings in match.innings.select_related("summary").order_by("innings_number"):
            summary = getattr(innings, "summary", None)
            if summary is None:
                continue
            summaries.append(
                {
                    "innings_number": innings.innings_number,
                    "runs": summary.runs,
                    "wickets": summary.wickets,
                    "overs_display": summary.overs_display,
                    "extras": summary.extras or {},
                }
            )
        return {"match_id": str(match.id), "summaries": summaries, "status": match.status}

    def iter_deliveries(self, provider_match_id: str, since_seq: int | None) -> Iterable[Any]:
        """Yield ``LiveInningsBatch`` objects after ``since_seq`` (fault-aware)."""

        self._calls += 1
        self._consume_error_budgets()
        if self.faults.slow_seconds:
            self.clock.advance(self.faults.slow_seconds)

        match = self._resolve_match(provider_match_id)

        # A duplicated delivery is queued on the call that serves it and
        # re-served on the next call (the pipeline must find it a no-op).
        if self._dup_queue:
            queued, self._dup_queue = self._dup_queue, []
            batches = self._batches_from_rows(queued, revision=1)
            return self._to_batches(batches)

        rows = self._recorded_rows(match, since_seq)
        if not rows and self.faults.correction and not self._corrections_served:
            # Stream fully consumed → the provider revision arrives now.
            self._corrections_served = True
            batches = self._batches_from_rows(
                self._recorded_rows(match, None, only_seqs=self.faults.correction),
                revision=2,
            )
            return self._to_batches(batches)

        rows = self._apply_reorder(rows)
        page_batches: dict[int, list[Any]] = {}
        for innings_number, seq, delivery in rows:
            if seq in set(self.faults.dropped):
                continue
            if seq in self._held and self._held[seq] > 0:
                self._held[seq] -= 1
                continue
            if not self._released(seq):
                continue
            if seq in set(self.faults.duplicate):
                self._dup_queue.append((innings_number, seq, delivery))
            draft = self._draft_for(delivery, seq)
            if seq in set(self.faults.malformed):
                draft = self._malformed(draft)
            page_batches.setdefault(innings_number, []).append(draft)
        return self._to_batches(page_batches)

    def get_commentary(self, provider_match_id: str) -> Iterable[Any]:
        return []

    def get_rankings(self, gender: str) -> Iterable[Any]:
        return []

    def normalize(self, raw: Any) -> Any:
        """Already-normalized records pass through (this is a fake provider)."""
        return raw

    # -- helpers ----------------------------------------------------------- #
    def _resolve_match(self, provider_match_id: str) -> Any:
        from apps.matches.models import Match

        match = Match.objects.filter(pk=provider_match_id).first()
        if match is None:
            match = Match.objects.filter(provider_match_id=provider_match_id).first()
        if match is None:
            raise ReplayAdapterError(f"no recorded match for {provider_match_id!r}")
        return match

    def _state_for(self, match: Any) -> Any:
        from apps.live.services.apply import LiveState

        return LiveState(
            status=match.status,
            state_title=match.state_title,
            status_detail=match.status_detail,
        )

    def _recorded_rows(
        self,
        match: Any,
        since_seq: int | None,
        *,
        only_seqs: tuple[int, ...] | None = None,
    ) -> list[tuple[int, int, Any]]:
        """Recorded ``(innings_number, seq, delivery)`` rows, oldest first."""
        from apps.deliveries.models import Delivery

        rows = (
            Delivery.objects.filter(match=match, is_current=True)
            .select_related("bowler", "batter", "non_striker", "dismissed_player", "innings")
            .order_by("innings__innings_number", "delivery_seq")
        )
        if since_seq is not None:
            rows = rows.filter(delivery_seq__gt=since_seq)
        if only_seqs:
            rows = rows.filter(delivery_seq__in=only_seqs)
        return [(row.innings.innings_number, row.delivery_seq, row) for row in rows]

    def _apply_reorder(self, rows: list[tuple[int, int, Any]]) -> list[tuple[int, int, Any]]:
        """Permute the first window when a ``reordered`` fault is scripted."""
        order = self.faults.reordered
        if not order:
            return rows
        block, rest = rows[: len(order)], rows[len(order) :]
        by_seq = {seq: (inn, seq, row) for inn, seq, row in block}
        try:
            ordered = [by_seq[seq] for seq in order]
        except KeyError:
            return rows
        return ordered + rest

    def _batches_from_rows(
        self, rows: list[tuple[int, int, Any]], *, revision: int
    ) -> dict[int, list[Any]]:
        batches: dict[int, list[Any]] = {}
        for innings_number, seq, delivery in rows:
            draft = self._draft_for(delivery, seq, revision=revision)
            if seq in set(self.faults.malformed):
                draft = self._malformed(draft)
            batches.setdefault(innings_number, []).append(draft)
        return batches

    def _to_batches(self, batches: dict[int, list[Any]]) -> list[Any]:
        from apps.live.services.apply import LiveInningsBatch

        return [
            LiveInningsBatch(innings_number=number, drafts=tuple(batches[number]))
            for number in sorted(batches)
        ]

    def _released(self, seq: int) -> bool:
        if not self.paced:
            return True
        release_at = self.clock.epoch + (seq - 1) * self.release_interval
        return self.clock.now() >= release_at

    def _consume_error_budgets(self) -> None:
        if self._outage_budget > 0:
            self._outage_budget -= 1
            raise UpstreamUnavailable("replay outage window")
        if self._retry_budget > 0:
            self._retry_budget -= 1
            raise RateLimited("replay rate limited", retry_after=15)
        if self._5xx_budget > 0:
            self._5xx_budget -= 1
            raise UpstreamUnavailable("replay upstream error")
        if self._timeout_budget > 0:
            self._timeout_budget -= 1
            raise TimeoutError("replay timed out")

    def _draft_for(self, delivery: Any, seq: int, *, revision: int | None = None) -> Any:
        from apps.deliveries.services.validation import DeliveryDraft

        return DeliveryDraft(
            delivery_seq=seq,
            over_number=delivery.over_number,
            ball_in_over=delivery.ball_in_over,
            bowler_id=delivery.bowler_id,
            batter_id=delivery.batter_id,
            non_striker_id=delivery.non_striker_id,
            runs_off_bat=delivery.runs_off_bat,
            wides=delivery.wides,
            noballs=delivery.noballs,
            byes=delivery.byes,
            leg_byes=delivery.leg_byes,
            penalty_runs=delivery.penalty_runs,
            is_wicket=delivery.is_wicket,
            dismissal_type=delivery.dismissal_type,
            dismissed_player_id=delivery.dismissed_player_id,
            bowler_credited_wicket=delivery.bowler_credited_wicket,
            fielder1_id=delivery.fielder1_id,
            fielder2_id=delivery.fielder2_id,
            fielder3_id=delivery.fielder3_id,
            shot_type=delivery.shot_type,
            commentary_text=delivery.commentary_text,
            provider_delivery_id=delivery.provider_delivery_id,
            revision=revision if revision is not None else delivery.revision,
            retired_player_id=delivery.retired_player_id,
            retirement_type=delivery.retirement_type,
        )

    def _malformed(self, draft: Any) -> Any:
        return replace(draft, dismissal_type="NOT_A_DISMISSAL")


def replay_available() -> bool:
    """True when the fake provider may be constructed (not prod)."""
    return getattr(settings, "ENVIRONMENT", "dev") != "prod"


__all__ = [
    "ReplayAdapter",
    "ReplayAdapterError",
    "ReplayClock",
    "ReplayDisabledInProduction",
    "ReplayFaults",
    "replay_available",
]
