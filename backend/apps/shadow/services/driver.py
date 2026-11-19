"""Shadow-mode driver: run the comparison pass, record metrics, queue findings,
and write the dated report (Phase I Task 2).

Gated on ``PROVIDER_SHADOW_MODE`` (default off). Side effects live here, not in
``apps.shadow.compare`` (which stays deterministic and side-effect free):
* metrics: per-category comparison/mismatch counters, ingest-latency histogram,
  correction frequency, coverage gaps, schema drift, unresolved identity;
* queue: every finding is handed to the registered reporter (the DLQ owner);
* report: ``reports/shadow/<day>.md|json`` via ``apps.shadow.services.report``.

Nothing is auto-corrected: a finding never writes to the match rows.

Replay-mode honesty: with the provider gate unmet, the "provider" and the
"second source" both read the recorded corpus, so a clean pass produces zero
mismatches (evidence the pipeline is internally consistent). Latency values are
~0 (provider event and event-log write share a transaction); a licensed
provider's real network/ingestion latency is UNVERIFIED.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from django.conf import settings

from apps.core import metrics
from apps.shadow.compare import CATEGORIES, compare_match, report_finding
from apps.shadow.services import report as report_service


def run_shadow_pass(*, day: date | None = None) -> int:
    """Compare all shadow-ingested completed matches; returns matches compared.

    Refuses to run when ``PROVIDER_SHADOW_MODE`` is off (the beat task and the
    management command rely on this).
    """
    if not getattr(settings, "PROVIDER_SHADOW_MODE", False):
        raise RuntimeError("shadow mode is off: set PROVIDER_SHADOW_MODE=true to run a shadow pass")
    from apps.matches.models import Match
    from apps.providers.adapters.replay import ReplayAdapter
    from apps.providers.models import DataProvider

    target_day = day or date.today()
    provider = DataProvider.objects.filter(code=_shadow_provider_code()).first()
    adapter = ReplayAdapter()

    matches = Match.objects.filter(
        status__in=("COMPLETED", "ABANDONED", "CANCELLED", "FORFEITED"),
        start_time_utc__date__lte=target_day,
        end_time_utc__date__gte=target_day - timedelta(days=1),
    ).order_by("start_time_utc")
    if provider is not None:
        matches = matches.filter(source_provider_id=provider.id)

    reports = []
    for match in matches:
        report = compare_match(match, adapter=adapter)
        reports.append(report)
        _record_comparison_metrics(report)
        for finding in report.findings:
            report_finding(finding)

    latency = _measure_latency(matches)
    corrections = _measure_corrections(matches)
    coverage_gaps = _count_coverage_gaps(matches, provider, target_day)
    schema_drift = getattr(adapter, "schema_drift_events", 0)
    metrics.SHADOW_SCHEMA_DRIFT.inc(schema_drift)
    unresolved = _count_unresolved_identity(provider)

    report_service.write_report(
        target_day,
        reports,
        latency=latency,
        corrections=corrections,
        coverage_gaps=coverage_gaps,
        schema_drift=schema_drift,
        unresolved_identity=unresolved,
    )
    return len(reports)


def _shadow_provider_code() -> str:
    return getattr(settings, "SHADOW_COMPARISON_PROVIDER", "replay")


def _record_comparison_metrics(report: Any) -> None:
    for category in CATEGORIES:
        metrics.SHADOW_COMPARISONS.labels(category=category).inc()
    for finding in report.findings:
        metrics.SHADOW_MISMATCHES.labels(category=finding.category, severity=finding.severity).inc()


def _measure_latency(matches: Any) -> dict[str, float]:
    """Provider-event-time → event-log-time distribution (p50/p90/p99).

    Provider event time is approximated by ``match.last_ingested_at`` (the
    stamp apply_batch writes when a batch changed the match); event-log time is
    the last ``match_event_log`` row's ``created_at``. Both are written in the
    same transaction for the replay adapter, so the measured values are ~0.
    """
    samples: list[float] = []
    for match in matches:
        last_event = match.event_log.order_by("-seq").first()
        ingested_at = getattr(match, "last_ingested_at", None)
        if last_event is None or ingested_at is None:
            continue
        delta = (last_event.created_at - ingested_at).total_seconds()
        samples.append(max(0.0, delta))
        metrics.SHADOW_INGEST_LATENCY.observe(max(0.0, delta))
    return _percentiles(samples)


def _measure_corrections(matches: Any) -> dict[str, Any]:
    """Correction frequency for the shadow matches.

    A corrected delivery is a current row with ``revision > 1`` (the live
    pipeline's supersession mechanism). Correction *lag* (provider revision
    time → our applied time) is not measurable in replay mode — there is no
    stored provider revision timestamp — so the lag distribution is reported
    as an empty map with a note.
    """
    from apps.deliveries.models import Delivery

    counts = Delivery.objects.filter(match__in=matches, is_current=True).exclude(revision=1).count()
    metrics.SHADOW_CORRECTIONS.inc(counts)
    return {
        "count": counts,
        "lag_distribution": {},
        "note": "correction lag is not measurable in replay mode (no provider "
        "revision timestamp); UNVERIFIED against a licensed provider",
    }


def _count_coverage_gaps(matches: Any, provider: Any, target_day: date) -> int:
    """Completed matches the shadow provider omitted or lists late.

    Gap = completed matches in the window that the shadow provider did *not*
    ingest (counted against the full window, not the already-filtered
    ``matches``). With the provider gate unmet the replay corpus is shared, so
    a clean pass over a provider-scoped corpus reports 0.
    """
    from apps.matches.models import Match

    if provider is None:
        gap = 0
    else:
        window_total = Match.objects.filter(
            status__in=("COMPLETED", "ABANDONED", "CANCELLED", "FORFEITED"),
            start_time_utc__date__lte=target_day,
            end_time_utc__date__gte=target_day - timedelta(days=1),
        ).count()
        gap = window_total - matches.count()
    metrics.SHADOW_COVERAGE_GAPS.inc(gap)
    return gap


def _count_unresolved_identity(provider: Any) -> int:
    from apps.providers.models import ProviderEntityMap

    qs = ProviderEntityMap.objects.filter(verified=False)
    if provider is not None:
        qs = qs.filter(provider=provider)
    count = qs.count()
    metrics.SHADOW_UNRESOLVED_IDENTITY.set(count)
    return count


def _percentiles(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {}
    ordered = sorted(samples)
    n = len(ordered)

    def pct(p: float) -> float:
        index = min(n - 1, max(0, round(p * (n - 1))))
        return round(ordered[index], 6)

    return {"p50": pct(0.50), "p90": pct(0.90), "p99": pct(0.99), "n": n}


__all__ = ["run_shadow_pass"]
