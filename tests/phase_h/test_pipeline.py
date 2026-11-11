"""Tier-2 fantasy recompute pipeline (Task 4).

Covers the plan's pipeline requirements: idempotence by (snapshot digest +
version hash), the calc-audit row contract (trigger, version hash, snapshot +
output digests, engine version, timings), append-only ledger with derived
current rows, per-player diffs on correction, ``fantasy.computed`` after
commit, provisional runs (off by default, labelled, never touching the final
ledger), and version isolation (v2 never alters v1).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.conf import settings
from django.test import override_settings

from apps.core import events as core_events
from apps.fantasy.engine import content_hash, point_rule
from apps.fantasy.models import (
    FantasyCalcAudit,
    FantasyPointsLedger,
    PlayerMatchPerformance,
)
from apps.fantasy.services import ledger, recompute
from apps.fantasy.services.recompute import ProvisionalDisabledError
from apps.fantasy.services.snapshot import build_snapshot, snapshot_from_payload

from .factories import add_stat, make_completed_match, make_ruleset_version

pytestmark = pytest.mark.django_db


def test_recompute_writes_audit_ledger_and_performances() -> None:
    match, team1, _ = make_completed_match()
    player, _ = add_stat(match, team1, runs=58, balls=42, out=True)
    _, version = make_ruleset_version()

    report = recompute.recompute_match(match, trigger="match.completed")

    snapshot = build_snapshot(match)
    spec = recompute.ruleset_spec_for(version)

    audit = FantasyCalcAudit.objects.get(match_id=match.id, ruleset_version_id=version.id)
    assert audit.trigger == "match.completed"
    assert audit.engine_version == settings.FANTASY_ENGINE_VERSION
    assert audit.version_hash == content_hash(spec)
    assert audit.input_digest == snapshot.digest
    assert audit.snapshot_digest == snapshot.digest
    assert audit.output_digest  # engine result digest
    assert audit.is_provisional is False
    assert audit.snapshot is not None
    assert snapshot_from_payload(audit.snapshot).digest == snapshot.digest
    assert audit.timings is not None
    assert "compute_ms" in audit.timings

    rows = FantasyPointsLedger.objects.filter(match_id=match.id)
    assert rows.count() == 1
    row = rows.get()
    assert row.player_id == player.id
    assert row.delta == Decimal("58.00")
    assert row.calc_run_id == audit.id
    assert row.event_type == "computed"

    perf = PlayerMatchPerformance.objects.get(
        match_id=match.id, player_id=player.id, ruleset_version_id=version.id
    )
    assert perf.total_points == Decimal("58.00")
    assert perf.calc_run_id == audit.id
    assert perf.revision == 1
    assert perf.is_partial is False
    assert perf.breakdown == {"runs": "58.00"}

    assert report.changed is True
    assert report.player_count == 1
    assert report.calc_run_ids == (str(audit.id),)
    assert report.snapshot_digest == snapshot.digest


def test_recompute_is_idempotent_noop() -> None:
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    _, _version = make_ruleset_version()

    first = recompute.recompute_match(match)
    second = recompute.recompute_match(match)

    assert second.changed is False
    assert FantasyCalcAudit.objects.filter(match_id=match.id).count() == 1
    assert FantasyPointsLedger.objects.filter(match_id=match.id).count() == 1
    perf = PlayerMatchPerformance.objects.get(match_id=match.id)
    assert perf.revision == 1
    assert second.calc_run_ids == ()
    assert first.calc_run_ids


def test_correction_that_changes_input_recomputes_with_diff() -> None:
    match, team1, _ = make_completed_match()
    player, stat = add_stat(match, team1, runs=58, balls=42, out=True)
    _, version = make_ruleset_version()
    recompute.recompute_match(match)

    stat.batting = [
        {**stat.batting[0], "runs": 70, "balls": 50},
    ]
    stat.save(update_fields=["batting"])

    report = recompute.recompute_match(match, trigger="correction.applied")

    assert report.changed is True
    assert report.diff is not None
    assert report.diff[str(player.id)] == Decimal("12.00")
    assert FantasyCalcAudit.objects.filter(match_id=match.id).count() == 2
    # Append-only: the old ledger row is untouched, the new one is current.
    assert FantasyPointsLedger.objects.filter(match_id=match.id).count() == 2
    assert ledger.sum_current(match.id, player.id, version.id) == Decimal("70.00")
    perf = PlayerMatchPerformance.objects.get(match_id=match.id, player_id=player.id)
    assert perf.total_points == Decimal("70.00")
    assert perf.revision == 2


def test_correction_that_changes_nothing_leaves_points_untouched() -> None:
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    _, _version = make_ruleset_version()
    recompute.recompute_match(match)

    report = recompute.recompute_match(match, trigger="correction.applied")

    assert report.changed is False
    assert FantasyCalcAudit.objects.filter(match_id=match.id).count() == 1
    assert FantasyPointsLedger.objects.filter(match_id=match.id).count() == 1


def test_version_v2_never_alters_v1_results() -> None:
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=40, balls=30, out=True)
    _, v1 = make_ruleset_version(version_number=1)
    _, v2 = make_ruleset_version(
        version_number=2, rules=(point_rule("runs", "per_unit", "2.00", "batting"),)
    )

    recompute.recompute_match(match, version_id=v1.id)
    v1_audit_count = FantasyCalcAudit.objects.filter(ruleset_version_id=v1.id).count()
    v1_ledger_count = FantasyPointsLedger.objects.filter(ruleset_version_id=v1.id).count()
    v1_total = PlayerMatchPerformance.objects.get(ruleset_version_id=v1.id).total_points
    assert v1_total == Decimal("40.00")

    recompute.recompute_match(match, version_id=v2.id)
    v2_total = PlayerMatchPerformance.objects.get(ruleset_version_id=v2.id).total_points
    assert v2_total == Decimal("80.00")

    assert FantasyCalcAudit.objects.filter(ruleset_version_id=v1.id).count() == v1_audit_count
    assert FantasyPointsLedger.objects.filter(ruleset_version_id=v1.id).count() == v1_ledger_count
    assert PlayerMatchPerformance.objects.get(ruleset_version_id=v1.id).total_points == Decimal(
        "40.00"
    )


def test_recompute_skips_versions_without_auto_recompute() -> None:
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    _, on = make_ruleset_version(auto_recompute=True)
    _, off = make_ruleset_version(version_number=2, auto_recompute=False)

    recompute.recompute_match(match)

    assert FantasyCalcAudit.objects.filter(ruleset_version_id=on.id).exists()
    assert not FantasyCalcAudit.objects.filter(ruleset_version_id=off.id).exists()


def test_summary_only_match_flagged_partial_with_unsupported_rules() -> None:
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=40, balls=30, out=True, is_partial=True)
    _, version = make_ruleset_version(
        rules=(
            point_rule("runs", "per_unit", "1.00", "batting"),
            point_rule("balls", "per_unit", "0.10", "batting"),
        )
    )

    recompute.recompute_match(match)

    perf = PlayerMatchPerformance.objects.get(ruleset_version_id=version.id)
    assert perf.is_partial is True
    assert "balls" in perf.unsupported_rules
    assert perf.total_points == Decimal("40.00")


def test_provisional_disabled_by_default() -> None:
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    with pytest.raises(ProvisionalDisabledError):
        recompute.recompute_match(match, provisional=True)


@override_settings(FANTASY_PROVISIONAL_ENABLED=True)
def test_provisional_run_is_labelled_and_never_touches_final_ledger() -> None:
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    _, version = make_ruleset_version()

    report = recompute.recompute_match(match, provisional=True, trigger="provisional")

    audit = FantasyCalcAudit.objects.get(
        match_id=match.id, ruleset_version_id=version.id, is_provisional=True
    )
    assert audit.is_provisional is True
    assert audit.trigger == "provisional"
    assert audit.snapshot is not None  # reproducible
    assert report.provisional is True
    # Provisional runs never write the final ledger or the read model.
    assert not FantasyPointsLedger.objects.filter(match_id=match.id).exists()
    assert not PlayerMatchPerformance.objects.filter(match_id=match.id).exists()

    # A final run is unaffected by the provisional one.
    recompute.recompute_match(match)
    assert FantasyPointsLedger.objects.filter(match_id=match.id).count() == 1
    assert FantasyCalcAudit.objects.filter(match_id=match.id, is_provisional=False).count() == 1


def test_recompute_returns_report_with_version_ids() -> None:
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=10, balls=6)
    _, v1 = make_ruleset_version()
    _, v2 = make_ruleset_version(
        version_number=2, rules=(point_rule("runs", "per_unit", "2.00", "batting"),)
    )

    report = recompute.recompute_match(match)

    assert set(report.versions_computed) == {str(v1.id), str(v2.id)}
    assert len(report.calc_run_ids) == 2


def test_celery_task_recomputes_and_returns_report_dict() -> None:
    from apps.fantasy.tasks import recompute_match_task

    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    make_ruleset_version()

    payload = recompute_match_task(str(match.id))

    assert payload is not None
    assert payload["match_id"] == str(match.id)
    assert payload["changed"] is True
    assert FantasyCalcAudit.objects.filter(match_id=match.id).count() == 1
    # Idempotent second delivery.
    assert recompute_match_task(str(match.id))["changed"] is False


def test_celery_task_missing_match_returns_none() -> None:
    from apps.fantasy.tasks import recompute_match_task

    assert recompute_match_task("00000000-0000-7000-0000-000000000000") is None


def test_fantasy_computed_registers_cache_invalidation() -> None:
    from apps.core.cache import invalidation

    namespaces = invalidation.namespaces_for(
        "fantasy.computed", {"match_id": "m1", "calc_run_id": "c1"}
    )
    assert "fantasy" in namespaces
    assert "fantasy:match:m1" in namespaces
    assert "fantasy.computed" in invalidation.registered_topics()


@pytest.mark.django_db(transaction=True)
def test_fantasy_computed_published_after_commit() -> None:
    from apps.core import events as core_events_module

    received: list[dict[str, object]] = []
    core_events_module.subscribe(core_events.TOPIC_FANTASY_COMPUTED, lambda p: received.append(p))
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    _, version = make_ruleset_version()

    recompute.recompute_match(match, trigger="match.completed")

    assert len(received) == 1
    assert received[0]["match_id"] == str(match.id)
    assert received[0]["calc_run_id"]
    assert (
        FantasyCalcAudit.objects.get(pk=received[0]["calc_run_id"]).ruleset_version_id == version.id
    )
