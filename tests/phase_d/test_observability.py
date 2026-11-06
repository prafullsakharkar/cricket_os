"""Observability surfaces (SECURITY §8/§10, ROADMAP Phase D Group 9 + Phase E 10).

* the internal ``/metrics`` exposition is settings-gated and renders the
  Prometheus text format (fallback facade in this offline image);
* ``ops/alerts/*.yml`` parses as Prometheus rules and every alert references a
  real registered metric and a runbook heading in ``docs/RUNBOOKS.md``
  (alert → runbook contract) — live-ingestion rules (Phase D) and
  realtime/cache rules (Phase E).
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest
import yaml
from django.test import override_settings
from rest_framework.test import APIClient

from apps.core import metrics

pytestmark = pytest.mark.django_db


def test_metrics_endpoint_serves_prometheus_exposition() -> None:
    client = APIClient()
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")
    assert response["Cache-Control"] == "no-cache"
    body = response.content.decode("utf-8")
    for name in (
        "match_freshness_seconds",
        "circuit_breaker_state",
        "dlq_depth",
        "shadow_verify_mismatches_total",
        "ingest_to_event_log_seconds",
        "event_publish_failures_total",
        # Phase E realtime + caching surfaces.
        "realtime_connections",
        "realtime_degraded",
        "realtime_dropped_frames_total",
        "realtime_gap_backfills_total",
        "realtime_resyncs_total",
        "realtime_cap_denied_total",
        "cache_hits_total",
        "cache_breaker_open",
        "cache_degraded_total",
        # Phase F statistics/analytics surfaces.
        "statistics_recompute_jobs_total",
        "statistics_recompute_errors_total",
        "statistics_recompute_seconds",
        "statistics_watermark_lag",
        "standings_reconciliation_mismatches_total",
        "leaderboard_rebuilds_total",
        "rankings_import_rows_total",
        "search_queries_total",
        "search_timeouts_total",
        "search_latency_seconds",
    ):
        assert name in body
    assert "# TYPE" in body  # exposition format, not JSON


def test_metrics_endpoint_is_gated_by_setting() -> None:
    """With the flag off the URL must not be registered at all (ops surface)."""

    def _has_metrics_path(module: object) -> bool:
        return any(getattr(pattern, "name", None) == "metrics" for pattern in module.urlpatterns)

    import config.urls as urls

    assert _has_metrics_path(urls)  # test settings enable it

    with override_settings(ENABLE_METRICS_ENDPOINT=False):
        reloaded = importlib.reload(urls)
        assert not _has_metrics_path(reloaded)
    # Restore the enabled URLconf for the rest of the suite.
    importlib.reload(urls)
    assert _has_metrics_path(importlib.import_module("config.urls"))


def test_metrics_facade_renders_after_observations() -> None:
    metrics.reset()
    metrics.BREAKER_STATE.labels(provider="replay", endpoint="poll").set(2)
    metrics.MATCH_FRESHNESS_SECONDS.labels(match_id="m-1").set(42)
    metrics.INGEST_LATENCY.observe(0.25)
    rendered = metrics.render()
    assert 'circuit_breaker_state{endpoint="poll",provider="replay"} 2' in rendered
    assert 'match_freshness_seconds{match_id="m-1"} 42' in rendered
    assert "ingest_to_event_log_seconds_count" in rendered
    assert "ingest_to_event_log_seconds_sum" in rendered
    metrics.reset()


# --------------------------------------------------------------------------- #
# Alert rules → metrics + runbooks contract
# --------------------------------------------------------------------------- #


def _load_alerts(path: str) -> list[dict[str, object]]:
    with Path(path).open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    return [rule for group in document["groups"] for rule in group["rules"]]


#: Alert -> exact rule set per file (protects against accidental rule edits).
_ALERT_FILES: dict[str, set[str]] = {
    "ops/alerts/live_alerts.yml": {
        "LiveMatchStale",
        "CircuitBreakerOpen",
        "DLQDepthHigh",
        "ShadowVerifyMismatch",
        "IngestLatencyHigh",
        "ProviderLagHigh",
        "EventPublishFailures",
    },
    "ops/alerts/realtime_alerts.yml": {
        "RealtimeDegraded",
        "RealtimeDroppedHigh",
        "RealtimeGapBackfills",
        "RealtimeResyncsHigh",
        "RealtimeCapDeniedHigh",
        "RealtimeConnectionsNearCap",
        "CacheBreakerOpen",
        "CacheDegraded",
        "CacheMissRatioHigh",
    },
    "ops/alerts/statistics_alerts.yml": {
        "StatisticsRecomputeErrors",
        "StatisticsWatermarkLagHigh",
        "StandingsReconciliationMismatch",
        "LeaderboardRebuildStorm",
        "SearchTimeoutHigh",
        "RankingsImportStalled",
    },
}


def _registered_metric_names() -> set[str]:
    """Registered base metric names (strip histogram suffixes for matching)."""
    rendered = metrics.render()
    names = set()
    for line in rendered.splitlines():
        if line.startswith("# TYPE "):
            names.add(line.split()[2])
    return names


def test_alert_rules_file_parses_and_cross_checks() -> None:
    registered = _registered_metric_names()

    # Every metric referenced by an alert expression exists in the facade.
    # Label names (match_id, provider, endpoint, le, instance) are not metrics.
    labels = {
        "by",
        "max",
        "sum",
        "rate",
        "increase",
        "histogram_quantile",
        "le",
        "match_id",
        "provider",
        "endpoint",
        "instance",
        "humanizePercentage",
        "value",
    }
    runbook_names = set()
    for path, expected in _ALERT_FILES.items():
        rules = _load_alerts(path)
        assert rules, f"no alert rules loaded from {path}"
        assert {rule["alert"] for rule in rules} == expected, path
        for rule in rules:
            expr = rule["expr"]
            for candidate in re.findall(r"\b[a-z_][a-z0-9_]+(?:_bucket|_count|_sum)?\b", expr):
                if candidate in labels:
                    continue
                if candidate in registered:
                    continue
                base = (
                    candidate.rsplit("_", 1)[0]
                    if candidate.endswith(("_bucket", "_count", "_sum"))
                    else candidate
                )
                assert base in registered, (
                    f"alert {rule['alert']} references unknown metric {candidate}"
                )
            runbook_names.add(rule["labels"]["runbook"])

    # Every alert maps to a runbook heading in docs/RUNBOOKS.md (headings are
    # Title Case; alert labels are lowercase identifiers).
    with Path("docs/RUNBOOKS.md").open(encoding="utf-8") as handle:
        runbook_doc = handle.read().lower()
    for name in runbook_names:
        assert f"## {name}" in runbook_doc, f"no runbook section for {name!r}"
