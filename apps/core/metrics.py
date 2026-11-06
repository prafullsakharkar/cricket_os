"""Lightweight metrics facade (SECURITY §8, PERFORMANCE §12).

The production target is ``prometheus_client``; this module delegates to it
when the package is installed. The offline build/test image does not ship it,
so a small in-process fallback with the same call shape keeps instrumentation
(and its tests) working without a network install. The fallback renders the
Prometheus text exposition format for the internal ``/metrics`` endpoint.

Cardinality rule (SECURITY §8): never label a metric with an unbounded id
except ``match_freshness``, which is capped by the caller to the live set.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any

try:  # pragma: no cover - exercised through the fallback in this image
    import prometheus_client as _prom  # type: ignore[import-not-found]

    _HAS_PROM = True
except ImportError:  # pragma: no cover - depends on the environment
    _prom = None
    _HAS_PROM = False


_LOCK = threading.Lock()
_VALUES: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
_KINDS: dict[str, str] = {}
_HELP: dict[str, str] = {}
_BUCKETS: dict[str, tuple[float, ...]] = {}


def _labels(items: dict[str, Any] | None) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(k), str(v)) for k, v in (items or {}).items()))


class Metric:
    """A counter/gauge/histogram handle with an optional real backend."""

    def __init__(
        self,
        name: str,
        documentation: str,
        *,
        kind: str = "counter",
        labelnames: tuple[str, ...] = (),
        buckets: tuple[float, ...] = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
    ) -> None:
        self.name = name
        self.documentation = documentation
        self.kind = kind
        self.labelnames = labelnames
        _KINDS[name] = kind
        _HELP[name] = documentation
        _BUCKETS[name] = buckets
        self._real: Any = None
        if _HAS_PROM:
            factory = {
                "counter": _prom.Counter,
                "gauge": _prom.Gauge,
                "histogram": _prom.Histogram,
            }[kind]
            kwargs: dict[str, Any] = {"labelnames": labelnames}
            if kind == "histogram":
                kwargs["buckets"] = buckets
            self._real = factory(name, documentation, **kwargs)

    def labels(self, **labels: Any) -> Metric:
        if self._real is not None:
            self._real = self._real.labels(**labels)
        self._labels = _labels(labels)
        return self

    @property
    def _key(self) -> tuple[str, tuple[tuple[str, str], ...]]:
        return (self.name, getattr(self, "_labels", ()))

    def inc(self, amount: float = 1.0) -> None:
        if self._real is not None:
            self._real.inc(amount)
            return
        with _LOCK:
            _VALUES[self._key] += amount

    def set(self, value: float) -> None:
        if self._real is not None:
            self._real.set(value)
            return
        with _LOCK:
            _VALUES[self._key] = value

    def observe(self, value: float) -> None:
        if self._real is not None:
            self._real.observe(value)
            return
        # Store a compact summary as ``count``/``sum``/``max`` labels in the
        # fallback so the exposition is still useful without a histogram impl.
        key = ("__hist__", self.name)
        with _LOCK:
            count, total, maximum = _HIST.get(key, (0.0, 0.0, 0.0))
            _HIST[key] = (count + 1, total + value, max(maximum, value))
        _HELP.setdefault(self.name, self.documentation)


_HIST: dict[tuple[str, str], tuple[float, float, float]] = {}
_REGISTRY: dict[str, Metric] = {}


def metric(
    name: str,
    documentation: str,
    *,
    kind: str = "counter",
    labelnames: tuple[str, ...] = (),
) -> Metric:
    """Return (and memoise) a metric handle by name."""
    existing = _REGISTRY.get(name)
    if existing is not None and existing.kind == kind:
        return existing
    created = Metric(name, documentation, kind=kind, labelnames=labelnames)
    _REGISTRY[name] = created
    return created


def reset() -> None:
    """Drop all values (test isolation; never called in production code)."""
    with _LOCK:
        _VALUES.clear()
        _HIST.clear()


def render() -> str:
    """Render the Prometheus text exposition format (internal ``/metrics``)."""
    if _HAS_PROM:
        return str(_prom.generate_latest().decode("utf-8"))  # pragma: no cover
    lines: list[str] = []
    for name, kind in sorted(_KINDS.items()):
        lines.append(f"# HELP {name} {_HELP.get(name, '')}")
        lines.append(f"# TYPE {name} {kind}")
        for (metric_name, labels), value in sorted(_VALUES.items()):
            if metric_name != name:
                continue
            label_text = ""
            if labels:
                rendered = ",".join(f'{k}="{_escape(v)}"' for k, v in labels)
                label_text = f"{{{rendered}}}"
            lines.append(f"{name}{label_text} {value}")
        hist = _HIST.get(("__hist__", name))
        if hist is not None:
            count, total, maximum = hist
            lines.append(f"{name}_count {int(count)}")
            lines.append(f"{name}_sum {total}")
            lines.append(f"{name}_max {maximum}")
    return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


# --------------------------------------------------------------------------- #
# Canonical metric names used across Phase D
# --------------------------------------------------------------------------- #
PROVIDER_LAG_SECONDS = metric(
    "provider_lag_seconds", "Seconds between provider update and ingestion", kind="gauge"
)
MATCH_FRESHNESS_SECONDS = metric(
    "match_freshness_seconds",
    "Seconds since the last committed live ingestion for a match",
    kind="gauge",
    labelnames=("match_id",),
)
DELIVERIES_PER_MINUTE = metric(
    "deliveries_per_minute", "Deliveries ingested in the last minute", kind="gauge"
)
JOBS_PER_MINUTE = metric(
    "jobs_per_minute", "Ingestion jobs completed in the last minute", kind="gauge"
)
INGESTION_ERRORS = metric(
    "ingestion_errors_total",
    "Permanent ingestion errors written to the DLQ",
    labelnames=("stage",),
)
DLQ_DEPTH = metric("dlq_depth", "Open (unresolved) DLQ rows", kind="gauge")
DUPLICATE_SUPPRESSED = metric(
    "duplicate_suppressed_total", "Duplicate deliveries/events suppressed as no-ops"
)
CORRECTIONS = metric("corrections_total", "Applied data corrections")
BREAKER_STATE = metric(
    "circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=half-open, 2=open)",
    kind="gauge",
    labelnames=("provider", "endpoint"),
)
SHADOW_VERIFY_MISMATCHES = metric(
    "shadow_verify_mismatches_total", "Incremental/full rebuild digest mismatches"
)
EVENT_PUBLISH_FAILURES = metric(
    "event_publish_failures_total", "Event-log rows that could not be published to Redis"
)
EVENT_LOG_LAG = metric(
    "event_log_lag", "Committed match events waiting to be published", kind="gauge"
)
INGEST_LATENCY = metric(
    "ingest_to_event_log_seconds", "Ingest-to-event-log latency", kind="histogram"
)
FRESHNESS_ALERTS = metric("freshness_alerts_total", "Stale live matches observed")

# Phase E — cache + realtime observability (PERFORMANCE §12, SECURITY §8).
CACHE_HITS = metric("cache_hits_total", "Read-through cache hits", labelnames=("namespace",))
CACHE_MISSES = metric("cache_misses_total", "Read-through cache misses", labelnames=("namespace",))
CACHE_STALE = metric(
    "cache_stale_served_total", "Stale entries served while revalidating", labelnames=("namespace",)
)
CACHE_DEGRADED = metric(
    "cache_degraded_total", "Requests served without the cache (breaker open or error)"
)
CACHE_INVALIDATIONS = metric(
    "cache_invalidations_total", "Cache namespaces invalidated by a domain event"
)
CACHE_PURGES = metric("cache_purges_total", "Surrogate-key purges sent to a CDN")
CACHE_BUILD_SECONDS = metric(
    "cache_build_seconds", "Time spent rebuilding a cache entry", kind="histogram"
)
CACHE_BREAKER_OPEN = metric(
    "cache_breaker_open", "Local cache breaker state (0=closed, 1=open)", kind="gauge"
)
# Phase F — statistics/analytics observability (ROADMAP Phase F Group 9).
STATISTICS_RECOMPUTE_JOBS = metric(
    "statistics_recompute_jobs_total",
    "Tier-2 recompute keys completed",
    labelnames=("scope",),
)
STATISTICS_RECOMPUTE_ERRORS = metric(
    "statistics_recompute_errors_total",
    "Tier-2 recompute keys that raised",
    labelnames=("scope",),
)
STATISTICS_RECOMPUTE_SECONDS = metric(
    "statistics_recompute_seconds",
    "Wall time per Tier-2 recompute key",
    kind="histogram",
)
STATISTICS_WATERMARK_LAG = metric(
    "statistics_watermark_lag",
    "Terminal matches whose aggregates are stale or missing",
    kind="gauge",
)
STANDINGS_RECONCILIATION_MISMATCHES = metric(
    "standings_reconciliation_mismatches_total",
    "Standings digests that disagreed with the provider (reconciliation jobs opened)",
)
LEADERBOARD_REBUILDS = metric(
    "leaderboard_rebuilds_total",
    "Digest-gated leaderboard rebuilds (rows bulk-replaced)",
    labelnames=("scope",),
)
RANKINGS_IMPORT_ROWS = metric(
    "rankings_import_rows_total",
    "Ranking rows imported, updated or skipped",
    labelnames=("status",),
)
SEARCH_QUERIES = metric(
    "search_queries_total",
    "Cross-entity search queries",
    labelnames=("types",),
)
SEARCH_TIMEOUTS = metric(
    "search_timeouts_total", "Cross-entity searches cancelled by the statement timeout"
)
SEARCH_LATENCY_SECONDS = metric(
    "search_latency_seconds",
    "Wall time per cross-entity search",
    kind="histogram",
)

# Realtime fan-out (API_SPEC §3.8, PERFORMANCE §8, SECURITY §8).
REALTIME_CONNECTIONS = metric(
    "realtime_connections", "Subscribed connections on this node", kind="gauge"
)
REALTIME_FANOUT_FRAMES = metric("realtime_fanout_frames_total", "Frames delivered to subscribers")
REALTIME_COALESCED = metric(
    "realtime_coalesced_batches_total", "Coalesced batch frames emitted (≤1 Hz)"
)
REALTIME_DROPPED = metric(
    "realtime_dropped_frames_total", "Frames dropped for a slow (full-queue) subscriber"
)
REALTIME_GAP_BACKFILLS = metric(
    "realtime_gap_backfills_total", "Missing seq runs replayed from the event log"
)
REALTIME_RESYNCS = metric(
    "realtime_resyncs_total", "resync_required frames sent (unreplayable position)"
)
REALTIME_INVALID_FRAMES = metric(
    "realtime_invalid_frames_total", "Broker messages rejected as malformed/oversized"
)
REALTIME_DEGRADED = metric(
    "realtime_degraded", "Realtime broker in degraded polling mode (0/1)", kind="gauge"
)
REALTIME_DRAINS = metric("realtime_drains_total", "Graceful hub drains performed")
REALTIME_CAP_DENIED = metric(
    "realtime_cap_denied_total",
    "Stream/WS connections rejected by the per-node connection caps",
)
# Throttling (API_SPEC §7).
RATE_LIMIT_DENIED = metric(
    "rate_limit_denied_total", "Requests denied by the token-bucket limiter", labelnames=("scope",)
)
RATE_LIMIT_FALLBACK = metric(
    "rate_limit_fallback_total",
    "Requests limited by the per-process fallback store (primary store down)",
)

__all__ = [
    "BREAKER_STATE",
    "CACHE_BREAKER_OPEN",
    "CACHE_BUILD_SECONDS",
    "CACHE_DEGRADED",
    "CACHE_HITS",
    "CACHE_INVALIDATIONS",
    "CACHE_MISSES",
    "CACHE_PURGES",
    "CACHE_STALE",
    "CORRECTIONS",
    "DELIVERIES_PER_MINUTE",
    "DLQ_DEPTH",
    "DUPLICATE_SUPPRESSED",
    "EVENT_LOG_LAG",
    "EVENT_PUBLISH_FAILURES",
    "FRESHNESS_ALERTS",
    "INGESTION_ERRORS",
    "INGEST_LATENCY",
    "JOBS_PER_MINUTE",
    "MATCH_FRESHNESS_SECONDS",
    "PROVIDER_LAG_SECONDS",
    "RATE_LIMIT_DENIED",
    "RATE_LIMIT_FALLBACK",
    "REALTIME_CAP_DENIED",
    "REALTIME_COALESCED",
    "REALTIME_CONNECTIONS",
    "REALTIME_DEGRADED",
    "REALTIME_DRAINS",
    "REALTIME_DROPPED",
    "REALTIME_FANOUT_FRAMES",
    "REALTIME_GAP_BACKFILLS",
    "REALTIME_INVALID_FRAMES",
    "REALTIME_RESYNCS",
    "SHADOW_VERIFY_MISMATCHES",
    "Metric",
    "metric",
    "render",
    "reset",
]
