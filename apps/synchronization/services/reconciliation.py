"""Reconciliation jobs (DATA_INGESTION §4.6, DOMAIN_MODEL §6.6).

Three kinds, one invariant: **mismatches are never auto-corrected silently** —
they surface as a ``ReconciliationJob`` (visible in admin) plus a redacted
``IngestionError`` DLQ row, and become a ``DataCorrection`` only after a
confirmation or an explicit provider revision.

* :func:`reconcile_checkpoint_gaps` — compare the checkpoint / stored
  ``delivery_seq`` set against the provider's innings length; report missing
  seqs so the caller re-fetches and backfills;
* :func:`reconcile_state` — diff the engine-rebuilt figures against the
  provider's final scorecard for a completed match;
* :func:`reconcile_fixtures` — matches in the window missing from the provider,
  or provider fixtures missing from us.

Every job is idempotent: re-running the same comparison reuses the open job for
that ``scope_key`` instead of piling up duplicates. Resolution is a manual
steward action (admin), never done here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from apps.synchronization.models import ReconciliationJob, SyncCheckpoint

#: Mismatch reporter registered by ``apps.ingestion`` (dependency inversion:
#: synchronization never imports ingestion; ingestion registers the DLQ writer
#: via :func:`register_mismatch_reporter` in its ``AppConfig.ready()``).
_mismatch_reporter = None


def register_mismatch_reporter(reporter: Any | None) -> None:
    """Set the callback that surfaces a reconciliation mismatch to ops.

    ``reporter(job, provider)`` receives the ``ReconciliationJob`` and the
    ``DataProvider`` (or ``None``). The default (no reporter) is a no-op, so
    the reconciliation service works standalone; a deployment that wires the
    ``ingestion`` app up writes the redacted ``IngestionError`` DLQ row.
    """
    global _mismatch_reporter
    _mismatch_reporter = reporter


def _report_mismatch(job: ReconciliationJob, *, provider: Any = None) -> None:
    if _mismatch_reporter is not None:
        _mismatch_reporter(job, provider=provider)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def open_job(
    scope_key: str,
    *,
    expected: dict[str, Any] | None,
    observed: dict[str, Any] | None,
    differences: dict[str, Any],
) -> ReconciliationJob:
    """Record (or refresh) an open job for ``scope_key``.

    Re-running the same reconciliation updates the existing open job — it does
    not open duplicates — and leaves resolved/ignored jobs untouched.
    """
    job, _ = ReconciliationJob.objects.update_or_create(
        scope_key=scope_key,
        status=ReconciliationJob.STATUS_OPEN,
        defaults={
            "expected_state": expected,
            "observed_state": observed,
            "differences": differences,
            "resolved_at": None,
        },
    )
    return job


def _error_for(job: ReconciliationJob, *, provider: Any = None) -> None:
    """Surface the mismatch to ops via the registered reporter (DLQ row)."""
    _report_mismatch(job, provider=provider)


def report_mismatch(job: ReconciliationJob, *, provider: Any = None) -> None:
    """Public alias for :func:`_error_for` (used by cross-app reconcilers).

    ``apps.statistics``' standings reconciliation opens a job and calls this
    directly; the reporter (and thus the DLQ ``IngestionError`` row) is wired
    by ``apps.ingestion``'s ``register_default_reporters``, so the statistics
    module never imports ingestion.
    """
    _report_mismatch(job, provider=provider)


def _stored_delivery_seqs(innings_id: Any) -> frozenset[int]:
    from apps.deliveries.models import Delivery

    return frozenset(
        Delivery.objects.filter(innings_id=innings_id, is_current=True).values_list(
            "delivery_seq", flat=True
        )
    )


@dataclass(frozen=True)
class GapReport:
    """Checkpoint/gap comparison result."""

    match_id: str
    innings_id: str | None
    provider_length: int
    stored: frozenset[int]
    missing: frozenset[int]
    job: ReconciliationJob | None = None

    def __bool__(self) -> bool:
        return bool(self.missing)


def reconcile_checkpoint_gaps(
    match: Any,
    *,
    provider: Any,
    innings_number: int = 1,
    provider_innings_length: int | None = None,
    provider_seq: Iterable[int] | None = None,
) -> GapReport:
    """Compare the stored delivery set against the provider's innings length.

    ``provider_seq`` (when supplied) is the exact set of delivery seqs the
    provider reports; otherwise the provider's innings length implies
    ``1..provider_innings_length``. A checkpoint that is behind is reported in
    the job's ``observed_state`` so an operator sees the resume position too.
    """
    from apps.innings.models import Innings

    innings = (
        Innings.objects.filter(match=match, innings_number=innings_number).order_by("id").first()
    )
    innings_id = str(innings.id) if innings is not None else None
    stored = _stored_delivery_seqs(innings.id) if innings is not None else frozenset()
    if provider_seq is not None:
        expected = frozenset(int(s) for s in provider_seq)
    elif provider_innings_length is not None:
        expected = frozenset(range(1, provider_innings_length + 1))
    else:
        raise ValueError("reconcile_checkpoint_gaps needs provider_seq or provider_innings_length")
    missing = expected - stored

    checkpoint: SyncCheckpoint | None = None
    if provider is not None:
        checkpoint = SyncCheckpoint.objects.filter(
            provider=provider, scope_key=f"match:{match.id}"
        ).first()

    report = GapReport(
        match_id=str(match.id),
        innings_id=innings_id,
        provider_length=len(expected),
        stored=stored,
        missing=missing,
    )
    if not missing:
        return report

    job = open_job(
        f"checkpoint:{match.id}:{innings_number}",
        expected={
            "provider_seq": sorted(expected),
            "provider_innings_length": provider_innings_length,
        },
        observed={
            "stored_seq": sorted(stored),
            "checkpoint_last_delivery_seq": checkpoint.last_delivery_seq
            if checkpoint is not None
            else None,
        },
        differences={"missing_seq": sorted(missing)},
    )
    _error_for(job, provider=provider)
    return GapReport(
        match_id=str(match.id),
        innings_id=innings_id,
        provider_length=len(expected),
        stored=stored,
        missing=missing,
        job=job,
    )


def _engine_figures(match: Any) -> dict[str, Any]:
    """Engine-derived figures (already rebuilt from stored deliveries)."""
    summaries: list[dict[str, Any]] = []
    for innings in match.innings.order_by("innings_number"):
        try:
            summary = innings.summary
        except Exception:
            continue
        summaries.append(
            {
                "innings_number": innings.innings_number,
                "runs": summary.runs,
                "wickets": summary.wickets,
                "extras": summary.extras or {},
            }
        )
    try:
        result = match.result
    except Exception:
        result = None
    return {
        "summaries": summaries,
        "result": {
            "result_type": result.result_type if result is not None else None,
            "winner_team_id": str(result.winner_team_id)
            if result and result.winner_team_id
            else None,
            "margin_type": result.margin_type if result is not None else None,
            "margin_value": result.margin_value if result is not None else None,
        },
    }


def _diff_figures(ours: dict[str, Any], theirs: dict[str, Any]) -> dict[str, Any]:
    """Compare engine figures to a provider card; return the differences."""
    differences: dict[str, Any] = {}
    ours_by_inn = {s["innings_number"]: s for s in ours.get("summaries", [])}
    theirs_by_inn = {s["innings_number"]: s for s in theirs.get("summaries", [])}
    for number in sorted(set(ours_by_inn) | set(theirs_by_inn)):
        a = ours_by_inn.get(number)
        b = theirs_by_inn.get(number)
        if a is None or b is None:
            differences[f"innings.{number}"] = {"ours": a, "provider": b}
            continue
        for key in ("runs", "wickets", "extras"):
            if a.get(key) != b.get(key):
                differences[f"innings.{number}.{key}"] = {
                    "ours": a.get(key),
                    "provider": b.get(key),
                }
    for key in ("result_type", "winner_team_id", "margin_type", "margin_value"):
        a = ours.get("result", {}).get(key)
        b = theirs.get("result", {}).get(key)
        if a != b:
            differences[f"result.{key}"] = {"ours": a, "provider": b}
    return differences


def reconcile_state(
    match: Any,
    *,
    provider_card: dict[str, Any],
    provider: Any = None,
) -> ReconciliationJob | None:
    """Diff a completed match's engine figures against the provider's card.

    A mismatch opens a ``ReconciliationJob`` and writes an
    ``IngestionError`` (``stage=reconciliation_mismatch``). No silent
    correction: a confirmed difference becomes a ``DataCorrection`` only when
    the provider explicitly pushes a revision through the correction service.
    """
    ours = _engine_figures(match)
    differences = _diff_figures(ours, provider_card)
    if not differences:
        return None
    job = open_job(
        f"state:{match.id}",
        expected={"provider_card": provider_card},
        observed=ours,
        differences=differences,
    )
    _error_for(job, provider=provider)
    return job


@dataclass(frozen=True)
class FixtureReport:
    """Fixture-window comparison result."""

    missing_from_provider: tuple[str, ...] = field(default_factory=tuple)
    missing_from_us: tuple[str, ...] = field(default_factory=tuple)
    jobs: tuple[ReconciliationJob, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:
        return bool(self.missing_from_provider or self.missing_from_us)


def reconcile_fixtures(
    provider: Any,
    *,
    window_start: datetime,
    window_end: datetime,
    provider_fixtures: Iterable[str],
) -> FixtureReport:
    """Matches in the window missing from the provider, or vice versa.

    ``provider_fixtures`` is the list of ``provider_match_id`` values the
    adapter reported for the window. Never deletes and never transitions —
    only records the divergence as jobs for a steward.
    """
    from apps.matches.models import Match

    provider_set = frozenset(provider_fixtures)
    ours = Match.objects.filter(
        source_provider_id=provider.id if provider is not None else None,
        start_time_utc__gte=window_start,
        start_time_utc__lt=window_end,
    )
    ours_by_pid = {m.provider_match_id: m for m in ours if m.provider_match_id}
    missing_from_us = tuple(sorted(provider_set - set(ours_by_pid)))
    missing_from_provider = tuple(sorted(set(ours_by_pid) - provider_set))

    jobs: list[ReconciliationJob] = []
    if missing_from_us:
        job = open_job(
            f"fixtures:{provider.id}:{window_start.isoformat()}:{window_end.isoformat()}",
            expected={"provider_fixtures": sorted(provider_set)},
            observed={"ours": sorted(ours_by_pid)},
            differences={"missing_from_us": sorted(missing_from_us)},
        )
        _error_for(job, provider=provider)
        jobs.append(job)
    if missing_from_provider:
        job = open_job(
            f"fixtures:{provider.id}:{window_start.isoformat()}:{window_end.isoformat()}",
            expected={"provider_fixtures": sorted(provider_set)},
            observed={"ours": sorted(ours_by_pid)},
            differences={"missing_from_provider": sorted(missing_from_provider)},
        )
        _error_for(job, provider=provider)
        jobs.append(job)
    return FixtureReport(
        missing_from_provider=missing_from_provider,
        missing_from_us=missing_from_us,
        jobs=tuple(jobs),
    )


def resolve_job(
    job: ReconciliationJob, *, status: str = ReconciliationJob.STATUS_RESOLVED
) -> ReconciliationJob:
    """Manually resolve/ignore an open job (steward action, admin)."""
    if job.status != ReconciliationJob.STATUS_OPEN:
        return job
    job.status = status
    job.resolved_at = _utcnow()
    job.save(update_fields=["status", "resolved_at"])
    return job


__all__ = [
    "FixtureReport",
    "GapReport",
    "open_job",
    "reconcile_checkpoint_gaps",
    "reconcile_fixtures",
    "reconcile_state",
    "register_mismatch_reporter",
    "report_mismatch",
    "resolve_job",
]
