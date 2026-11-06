"""Phase F Group 9: statistics/analytics observability.

The Phase F surfaces are instrumented through the ``apps.core.metrics`` facade:
recompute jobs/errors/latency, the sweep watermark-lag gauge, standings
reconciliation mismatches, leaderboard rebuilds, rankings imports and search
queries/timeouts/latency. These tests prove the instrumentation actually fires;
the ``ops/alerts/statistics_alerts.yml`` rules that read these metrics are
cross-checked against the registered names + runbooks by
``tests/phase_d/test_observability.py``.
"""

from __future__ import annotations

from datetime import date

import pytest

from apps.core import metrics
from apps.search.services.search import cross_entity_search

from ..phase_c.factories import make_player, make_team

pytestmark = pytest.mark.django_db


def _rendered() -> str:
    return metrics.render()


def test_recompute_records_job_counter_latency_and_errors() -> None:
    from apps.statistics.services import recompute
    from tests.phase_f.test_recompute import make_completed_match, make_stat

    match, team1, _team2 = make_completed_match()
    p = make_player("Observable Batter")
    make_stat(match, p, team1, runs=30, balls=20, out=True)
    recompute.recompute_match(match)

    rendered = _rendered()
    assert 'statistics_recompute_jobs_total{scope="career"} ' in rendered
    assert "statistics_recompute_seconds_count" in rendered
    assert "statistics_recompute_seconds_sum" in rendered


def test_recompute_error_counter_fires_and_re_raises() -> None:
    from apps.statistics.services import recompute

    # A malformed player id makes _recompute_career_key raise before any write.
    with pytest.raises(ValueError):
        recompute.recompute_keys(["career:not-a-uuid:CAREER:-:-:M"])

    rendered = _rendered()
    assert 'statistics_recompute_errors_total{scope="career"} 1' in rendered
    # The failed key is never counted as a completed job (only TYPE/HELP lines
    # render for a registered-but-never-incremented counter).
    assert not any(
        line.startswith("statistics_recompute_jobs_total{") for line in rendered.splitlines()
    )


def test_sweep_stamps_watermark_lag_gauge() -> None:
    from apps.statistics.services import recompute
    from tests.phase_f.test_recompute import make_completed_match

    make_completed_match()  # a completed match with no watermark yet
    healed = recompute.sweep_stale_statistics()
    assert healed >= 1
    assert "statistics_watermark_lag 1" in _rendered()


def test_standings_mismatch_increments_reconciliation_counter() -> None:
    from apps.statistics.services import recompute
    from tests.phase_c.factories import make_provider, make_team
    from tests.phase_f.test_standings import (
        _provider_rows,
        make_series,
        make_series_result,
        recompute_standings,
    )

    series = make_series()
    india, australia = make_team("India"), make_team("Australia")
    make_series_result(series, india, australia, india, "WIN", start_date=date(2026, 2, 1))
    recompute_standings(series)

    wrong = _provider_rows(series)
    wrong[str(india.id)]["points"] = 99
    job = recompute.reconcile_standings(series.id, wrong, provider=make_provider())
    assert job is not None
    assert "standings_reconciliation_mismatches_total 1" in _rendered()


def test_leaderboard_rebuild_counter_fires() -> None:
    from apps.statistics.services import recompute
    from tests.phase_f.test_recompute import make_completed_match, make_stat

    match, team1, _team2 = make_completed_match()
    p = make_player("Lb Observable")
    make_stat(match, p, team1, runs=20, wickets=1)
    recompute.recompute_match(match)

    assert 'leaderboard_rebuilds_total{scope="CAREER"} ' in _rendered()


def test_rankings_import_records_rows_by_status() -> None:
    from apps.providers.registry import list_providers, register_adapter
    from apps.rankings.adapters import FixtureRankingsAdapter
    from apps.rankings.services.import_rankings import import_rankings
    from tests.phase_f.test_rankings import _seed_corpus

    # Phase D tests clear the adapter registry; re-register like the rankings
    # suite does (ready() runs only once).
    if "fixture" not in list_providers():
        register_adapter("fixture", FixtureRankingsAdapter)

    provider, _players = _seed_corpus()
    import_rankings(provider)

    rendered = _rendered()
    assert 'rankings_import_rows_total{status="imported"} 5' in rendered
    assert 'rankings_import_rows_total{status="skipped_unresolved"} 1' in rendered


def test_search_records_query_and_latency() -> None:
    make_player("Virat Kohli")
    make_team("India")

    cross_entity_search("virat", types=("player",))

    rendered = _rendered()
    assert 'search_queries_total{types="player"} 1' in rendered
    assert "search_latency_seconds_count" in rendered
