"""Fantasy pipeline observability (Task 8).

Registers the fantasy metric family on the Phase D facade (``apps.core.metrics``)
so the internal ``/metrics`` endpoint renders the same Prometheus text
exposition. Cardinality rule (SECURITY §8): labels carry only bounded values —
``fantasy_unsupported_rule_counts{metric=...}`` uses the engine's closed
22-metric registry, never an unbounded id.

The three alert rules that consume these names live in
``ops/alerts/fantasy_alerts.yml``; their runbook anchors are in
``docs/RUNBOOKS.md`` (``## Fantasy pipeline``).
"""

from __future__ import annotations

from apps.core import metrics as core_metrics

#: Completed matches whose latest calc run is missing or stale, stamped by the
#: sweep. Drives ``FantasyStaleCalc`` (mirrors ``statistics_watermark_lag``).
FANTASY_RECOMPUTE_LAG = core_metrics.metric(
    "fantasy_recompute_lag",
    "Completed matches whose latest fantasy calc run is missing or stale",
    kind="gauge",
)

#: Messages waiting on the tier-2 queue (enqueued, not yet computed).
FANTASY_QUEUE_DEPTH = core_metrics.metric(
    "fantasy_queue_depth",
    "Fantasy recompute messages waiting on the tier-2 queue",
    kind="gauge",
)

#: Stale (match, version) pairs the sweep healed on its last run.
FANTASY_SWEEP_FINDINGS = core_metrics.metric(
    "fantasy_sweep_findings",
    "Stale fantasy calc pairs healed by the last sweep",
    kind="gauge",
)

#: Wall time per ``recompute_match`` call (histogram).
FANTASY_RECOMPUTE_SECONDS = core_metrics.metric(
    "fantasy_recompute_seconds",
    "Wall time per fantasy recompute_match call",
    kind="histogram",
)

#: Recomputed calls that raised (the pipeline fails loudly). Drives
#: ``FantasyRecomputeFailures``. Added beyond the plan's metric list because
#: the alert set requires a failure signal (D14).
FANTASY_RECOMPUTE_FAILURES = core_metrics.metric(
    "fantasy_recompute_failures_total",
    "Fantasy recompute calls that raised",
)

#: Fraction of the sweep-healed runs computed from partial source data (0..1).
FANTASY_PARTIAL_RATE = core_metrics.metric(
    "fantasy_partial_rate",
    "Fraction of sweep-healed fantasy runs computed from partial data",
    kind="gauge",
)

#: Rules skipped because the source snapshot lacked the metric. The label is
#: the engine metric name from the closed registry (bounded cardinality).
FANTASY_UNSUPPORTED_RULE_COUNTS = core_metrics.metric(
    "fantasy_unsupported_rule_counts",
    "Fantasy rules skipped because the source data lacked the metric",
    labelnames=("metric",),
)

#: Cumulative append-only ledger rows written (gauge, monotonically growing).
FANTASY_LEDGER_GROWTH = core_metrics.metric(
    "fantasy_ledger_growth",
    "Fantasy points ledger rows appended",
    kind="gauge",
)

#: Findings recorded by ``fantasy_verify``. Drives ``FantasyVerifyMismatch``.
FANTASY_VERIFY_MISMATCHES = core_metrics.metric(
    "fantasy_verify_mismatches_total",
    "Findings recorded by fantasy_verify against the calc trail",
)

__all__ = [
    "FANTASY_LEDGER_GROWTH",
    "FANTASY_PARTIAL_RATE",
    "FANTASY_QUEUE_DEPTH",
    "FANTASY_RECOMPUTE_FAILURES",
    "FANTASY_RECOMPUTE_LAG",
    "FANTASY_RECOMPUTE_SECONDS",
    "FANTASY_SWEEP_FINDINGS",
    "FANTASY_UNSUPPORTED_RULE_COUNTS",
    "FANTASY_VERIFY_MISMATCHES",
]
