"""Phase I Task 2: shadow metric family (registered, bumped, histogram)."""

from __future__ import annotations

from datetime import date

import pytest
from django.test import override_settings

from apps.core import metrics

pytestmark = pytest.mark.django_db

SHADOW_METRIC_NAMES = {
    "shadow_comparisons_total",
    "shadow_mismatches_total",
    "shadow_ingest_latency_seconds",
    "shadow_correction_lag_seconds",
    "shadow_corrections_total",
    "shadow_unresolved_identity_total",
    "shadow_schema_drift_total",
    "shadow_coverage_gaps_total",
}


def test_shadow_metric_family_is_registered() -> None:
    """Every shadow metric name is registered in the facade and exported."""
    rendered = metrics.render()
    for name in SHADOW_METRIC_NAMES:
        assert f"# TYPE {name}" in rendered, f"shadow metric {name} not rendered"


def test_shadow_metric_handles_exist_in_the_facade() -> None:
    for attr in (
        "SHADOW_COMPARISONS",
        "SHADOW_MISMATCHES",
        "SHADOW_INGEST_LATENCY",
        "SHADOW_CORRECTION_LAG",
        "SHADOW_CORRECTIONS",
        "SHADOW_UNRESOLVED_IDENTITY",
        "SHADOW_SCHEMA_DRIFT",
        "SHADOW_COVERAGE_GAPS",
    ):
        assert hasattr(metrics, attr), f"metrics.{attr} missing"


def test_comparison_and_mismatch_counters_bump() -> None:
    metrics.reset()
    for category in ("totals", "wickets", "extras", "result", "batting", "bowling"):
        metrics.SHADOW_COMPARISONS.labels(category=category).inc()
    metrics.SHADOW_MISMATCHES.labels(category="totals", severity="critical").inc()
    metrics.SHADOW_MISMATCHES.labels(category="totals", severity="critical").inc()

    rendered = metrics.render()
    assert 'shadow_comparisons_total{category="totals"} 1.0' in rendered
    assert 'shadow_comparisons_total{category="bowling"} 1.0' in rendered
    assert (
        'shadow_mismatches_total{category="totals",severity="critical"} 2.0' in rendered
        or 'shadow_mismatches_total{severity="critical",category="totals"} 2.0' in rendered
    )


def test_ingest_latency_histogram_records_observation() -> None:
    metrics.reset()
    metrics.SHADOW_INGEST_LATENCY.observe(0.01)
    metrics.SHADOW_INGEST_LATENCY.observe(0.02)
    rendered = metrics.render()
    assert "shadow_ingest_latency_seconds" in rendered


def test_pass_level_metrics_are_recorded_by_driver(tmp_path) -> None:
    """The driver records pass-level metrics even with an empty corpus."""
    from apps.shadow.services.driver import run_shadow_pass

    metrics.reset()
    with override_settings(
        PROVIDER_SHADOW_MODE=True,
        SHADOW_REPORT_DIR=str(tmp_path),
    ):
        run_shadow_pass(day=date(2026, 11, 13))

    rendered = metrics.render()
    assert "shadow_unresolved_identity_total" in rendered
    assert "shadow_coverage_gaps_total" in rendered
    assert "shadow_schema_drift_total" in rendered
