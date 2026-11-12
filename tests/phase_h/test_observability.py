"""Fantasy observability (Task 8).

The fantasy pipeline exposes its own metric family (``apps/fantasy/metrics.py``)
alongside the Phase D facade, three Prometheus alert rules
(``ops/alerts/fantasy_alerts.yml``), and runbook sections in
``docs/RUNBOOKS.md``. This suite proves:

* the metric handles are registered and render through the internal
  ``/metrics`` exposition;
* the pipeline actually instruments them — recompute latency, failures,
  unsupported-rule counts, partial rate, sweep lag/findings, ledger growth,
  verify mismatches and queue depth;
* the alert rules reference only registered metrics and every runbook anchor
  exists (the generic cross-check lives in tests/phase_d).

Cardinality rule: ``fantasy_unsupported_rule_counts`` is labelled by metric
name from the engine's closed 22-metric registry — never an unbounded id.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from unittest import mock

import pytest
import yaml
from rest_framework.test import APIClient

from apps.core import metrics as core_metrics
from apps.fantasy.engine import ContradictoryRulesError, point_rule
from apps.fantasy.models import FantasyCalcAudit, FantasyPointsLedger
from apps.fantasy.services import recompute
from apps.fantasy.services.adjustments import apply_adjustment

from .factories import add_stat, make_completed_match, make_ruleset_version

pytestmark = pytest.mark.django_db

#: Every metric the fantasy module registers (Task 8 plan).
FANTASY_METRIC_NAMES = (
    "fantasy_recompute_lag",
    "fantasy_queue_depth",
    "fantasy_sweep_findings",
    "fantasy_recompute_seconds",
    "fantasy_recompute_failures_total",
    "fantasy_partial_rate",
    "fantasy_unsupported_rule_counts",
    "fantasy_ledger_growth",
    "fantasy_verify_mismatches_total",
)

#: The exact alert set in ops/alerts/fantasy_alerts.yml.
FANTASY_ALERTS = {"FantasyStaleCalc", "FantasyRecomputeFailures", "FantasyVerifyMismatch"}


@pytest.fixture(autouse=True)
def _reset_metrics() -> None:
    """Each observability test starts from a clean metric table."""
    core_metrics.reset()
    yield
    core_metrics.reset()


def _render() -> str:
    return core_metrics.render()


# --------------------------------------------------------------------------- #
# Registration + exposition
# --------------------------------------------------------------------------- #


def test_fantasy_metrics_registered_and_render() -> None:
    body = _render()
    for name in FANTASY_METRIC_NAMES:
        assert f"# TYPE {name} " in body, name
    # Histograms render count/sum lines once observed; the TYPE line exists.
    assert "# TYPE fantasy_recompute_seconds histogram" in body


def test_metrics_endpoint_serves_fantasy_surfaces() -> None:
    client = APIClient()
    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.content.decode("utf-8")
    for name in FANTASY_METRIC_NAMES:
        assert name in body


def test_unsupported_rule_counts_carry_bounded_metric_label() -> None:
    from apps.fantasy import metrics

    metrics.FANTASY_UNSUPPORTED_RULE_COUNTS.labels(metric="balls").inc()
    assert 'fantasy_unsupported_rule_counts{metric="balls"} 1' in _render()


# --------------------------------------------------------------------------- #
# Pipeline instrumentation
# --------------------------------------------------------------------------- #


def test_recompute_observes_latency_and_partial_rate() -> None:
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    make_ruleset_version(auto_recompute=True)

    recompute.recompute_match(match)

    body = _render()
    assert "fantasy_recompute_seconds_count 1" in body
    assert "fantasy_recompute_seconds_sum" in body
    # One version computed, not partial -> rate 0.
    assert "fantasy_partial_rate 0" in body


def test_failed_recompute_counts_failures_and_raises() -> None:
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    # Storable but contradictory rules: per_unit + capped accrue the same runs.
    _, version = make_ruleset_version(
        rules=(
            point_rule("runs", "per_unit", "1.00", "batting"),
            point_rule("runs", "capped", "2.00", "batting", cap_value="10.00"),
        ),
        auto_recompute=True,
    )

    with pytest.raises(ContradictoryRulesError):
        recompute.recompute_match(match)

    assert "fantasy_recompute_failures_total 1" in _render()
    assert not FantasyCalcAudit.objects.filter(ruleset_version_id=version.id).exists()


def test_unsupported_rules_counted_by_metric_name() -> None:
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=40, balls=30, out=True, is_partial=True)
    make_ruleset_version(
        rules=(
            point_rule("runs", "per_unit", "1.00", "batting"),
            point_rule("balls", "per_unit", "0.10", "batting"),
        ),
        auto_recompute=True,
    )

    recompute.recompute_match(match)

    body = _render()
    assert 'fantasy_unsupported_rule_counts{metric="balls"} 1' in body


def test_sweep_stamps_lag_and_findings() -> None:
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    make_ruleset_version(auto_recompute=True)
    assert not FantasyCalcAudit.objects.filter(match_id=match.id).exists()

    healed = recompute.sweep_stale_fantasy()

    assert healed == 1
    body = _render()
    # Watermark semantics (statistics precedent): the sweep observed 1 stale
    # pair (lag) and healed it (findings). A healthy next run re-stamps lag 0.
    assert "fantasy_sweep_findings 1" in body
    assert "fantasy_recompute_lag 1" in body


def test_verify_counts_mismatches() -> None:
    match, team1, _ = make_completed_match()
    player, _ = add_stat(match, team1, runs=58, balls=42, out=True)
    _, version = make_ruleset_version(auto_recompute=True)
    recompute.recompute_match(match)

    # A bogus current ledger row for the same run (INSERT is the only legal
    # mutation) makes verify disagree with the stored run.
    audit = (
        FantasyCalcAudit.objects.filter(match_id=match.id).order_by("-computed_at", "-id").first()
    )
    FantasyPointsLedger.objects.create(
        match_id=match.id,
        player_id=player.id,
        ruleset_version_id=version.id,
        delta=Decimal("999.00"),
        event_type="computed",
        calc_run_id=audit.id,
    )

    findings = recompute.verify_fantasy("all")

    assert findings
    assert f"fantasy_verify_mismatches_total {len(findings)}" in _render()


def test_adjustment_does_not_disturb_pipeline_metrics() -> None:
    """Adjustments are a separate append-only ledger: applying one must not
    observe the recompute histogram or count a failure or a mismatch."""
    from django.contrib.auth.models import Group, User

    match, team1, _ = make_completed_match()
    player, _ = add_stat(match, team1, runs=58, balls=42, out=True)
    make_ruleset_version(auto_recompute=True)
    recompute.recompute_match(match)
    core_metrics.reset()

    user = User.objects.create_user(username="adj-obs", password="pw")
    user.groups.add(Group.objects.get(name="data_steward"))
    apply_adjustment(
        match_id=match.id,
        player_id=player.id,
        delta=Decimal("5.00"),
        reason="observability test",
        reference="REF-OBS",
        actor=user,
    )

    body = _render()

    def value_lines(name: str) -> list[str]:
        # Value lines start with the bare metric name (HELP/TYPE lines carry a
        # leading "# "), so a metric with no observations has no value line.
        return [line for line in body.splitlines() if line.startswith(f"{name} ")]

    assert value_lines("fantasy_recompute_seconds_count") == []
    assert value_lines("fantasy_recompute_failures_total") == []
    assert value_lines("fantasy_verify_mismatches_total") == []


# --------------------------------------------------------------------------- #
# Queue depth
# --------------------------------------------------------------------------- #


def test_queue_depth_tracks_pending_enqueues() -> None:
    from apps.fantasy.tasks import enqueue_recompute, recompute_match_task

    # The task body is stubbed so the two enqueues stay pending.
    with mock.patch("apps.fantasy.tasks.recompute_match_task") as task:
        task.delay.return_value = None
        enqueue_recompute("11111111-1111-7111-8111-111111111111")
        enqueue_recompute("22222222-2222-7222-8222-222222222222")
        assert "fantasy_queue_depth 2" in _render()

    # The real task body decrements when it actually runs (eager path).
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=10, balls=6, out=True)
    make_ruleset_version(auto_recompute=True)
    recompute_match_task(str(match.id))
    assert "fantasy_queue_depth 1" in _render()


# --------------------------------------------------------------------------- #
# Alerts -> metrics + runbooks contract (scoped to fantasy)
# --------------------------------------------------------------------------- #


def _load_fantasy_alerts() -> list[dict[str, object]]:
    with Path("ops/alerts/fantasy_alerts.yml").open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    return [rule for group in document["groups"] for rule in group["rules"]]


def test_fantasy_alert_rules_exact_set() -> None:
    rules = _load_fantasy_alerts()
    assert {rule["alert"] for rule in rules} == FANTASY_ALERTS


def test_fantasy_alert_exprs_reference_registered_metrics() -> None:
    registered = {line.split()[2] for line in _render().splitlines() if line.startswith("# TYPE ")}
    reserved = {"increase", "rate", "sum", "max", "by", "le", "value"}
    for rule in _load_fantasy_alerts():
        for candidate in re.findall(r"\b[a-z_][a-z0-9_]+(?:_bucket|_count|_sum)?\b", rule["expr"]):
            if candidate not in reserved:
                assert candidate in registered, f"{rule['alert']} -> {candidate}"


def test_fantasy_alert_runbook_anchors_exist() -> None:
    doc = Path("docs/RUNBOOKS.md").read_text(encoding="utf-8").lower()
    for rule in _load_fantasy_alerts():
        anchor = rule["labels"]["runbook"]
        assert f"## {anchor}" in doc, f"no runbook section for {anchor!r}"
