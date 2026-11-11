"""Sweep healing for stale fantasy calc runs (Task 4).

A recompute that crashed mid-flight, or a source change that landed behind the
pipeline's back (no completion/correction event), leaves the latest calc run
stale. ``sweep_stale_fantasy`` detects the staleness by re-building the
snapshot digest and comparing it to the stored input digest (and the version's
content hash), then heals. ``sweep_stale_statistics`` (Phase F) is extended to
drive the fantasy sweep too.
"""

from __future__ import annotations

import pytest

from apps.fantasy.models import FantasyCalcAudit
from apps.fantasy.services import recompute
from apps.fantasy.services.snapshot import build_snapshot

from .factories import add_stat, make_completed_match, make_ruleset_version

pytestmark = pytest.mark.django_db


def test_sweep_heals_a_stale_calc_run() -> None:
    match, team1, _ = make_completed_match()
    _player, stat = add_stat(match, team1, runs=58, balls=42, out=True)
    _, _version = make_ruleset_version()

    recompute.recompute_match(match)
    assert FantasyCalcAudit.objects.filter(match_id=match.id).count() == 1

    # The source changes behind the pipeline's back (no event fired).
    stat.batting = [{**stat.batting[0], "runs": 77}]
    stat.save(update_fields=["batting"])

    healed = recompute.sweep_stale_fantasy()

    assert healed == 1
    assert FantasyCalcAudit.objects.filter(match_id=match.id).count() == 2
    latest = (
        FantasyCalcAudit.objects.filter(match_id=match.id).order_by("-computed_at", "-id").first()
    )
    assert latest.trigger == "sweep"
    assert latest.input_digest == build_snapshot(match).digest


def test_sweep_skips_up_to_date_matches() -> None:
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    make_ruleset_version()
    recompute.recompute_match(match)

    assert recompute.sweep_stale_fantasy() == 0
    assert FantasyCalcAudit.objects.filter(match_id=match.id).count() == 1


def test_sweep_ignores_matches_with_no_auto_recompute_versions() -> None:
    match, team1, _ = make_completed_match()
    add_stat(match, team1, runs=58, balls=42, out=True)
    make_ruleset_version(auto_recompute=False)

    assert recompute.sweep_stale_fantasy() == 0
    assert not FantasyCalcAudit.objects.filter(match_id=match.id).exists()


def test_statistics_sweep_drives_fantasy_sweep() -> None:
    from apps.statistics.services import recompute as stats_recompute

    match, team1, _ = make_completed_match()
    _player, stat = add_stat(match, team1, runs=58, balls=42, out=True)
    _, _version = make_ruleset_version()

    recompute.recompute_match(match)
    stat.batting = [{**stat.batting[0], "runs": 66}]
    stat.save(update_fields=["batting"])

    healed = stats_recompute.sweep_stale_statistics()

    assert healed >= 1
    latest = (
        FantasyCalcAudit.objects.filter(match_id=match.id).order_by("-computed_at", "-id").first()
    )
    assert latest.input_digest == build_snapshot(match).digest
