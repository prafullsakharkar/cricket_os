"""Phase D: observability surfaces (SECURITY §8/§10, ROADMAP Phase D Group 9).

* the internal ``/metrics`` exposition is settings-gated and renders the
  Prometheus text format (fallback facade in this offline image);
* ``ops/alerts/live_alerts.yml`` parses as Prometheus rules and every alert
  references a real registered metric and a runbook heading in
  ``docs/RUNBOOKS.md`` (alert → runbook contract).
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


def _load_alerts() -> list[dict[str, object]]:
    with Path("ops/alerts/live_alerts.yml").open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    return [rule for group in document["groups"] for rule in group["rules"]]


def _registered_metric_names() -> set[str]:
    """Registered base metric names (strip histogram suffixes for matching)."""
    rendered = metrics.render()
    names = set()
    for line in rendered.splitlines():
        if line.startswith("# TYPE "):
            names.add(line.split()[2])
    return names


def test_alert_rules_file_parses_and_cross_checks() -> None:
    rules = _load_alerts()
    assert rules, "no alert rules loaded"
    expected = {
        "LiveMatchStale",
        "CircuitBreakerOpen",
        "DLQDepthHigh",
        "ShadowVerifyMismatch",
        "IngestLatencyHigh",
        "ProviderLagHigh",
        "EventPublishFailures",
    }
    assert {rule["alert"] for rule in rules} == expected

    registered = _registered_metric_names()

    # Every metric referenced by an alert expression exists in the facade.
    # Label names (match_id, provider, endpoint, le) are not metrics.
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
    }
    runbook_names = set()
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
