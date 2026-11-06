"""Phase F Group 4: leaderboard + form-trend population.

Covers the brief's leaderboard requirements: metric-ranked population with a
stable tie-break policy, per-scope/per-format isolation, digest idempotence,
team leaderboards, dependency-key wiring, and rolling form windows with the
per-match series.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.statistics.models import FormTrend, Leaderboard
from apps.statistics.services import recompute

from .test_recompute import make_completed_match, make_player, make_stat

pytestmark = pytest.mark.django_db


def _leaderboard(metric: str, *, scope: str = "CAREER", fmt: str | None = None):
    qs = Leaderboard.objects.filter(scope_type=scope, metric=metric, format_type=fmt)
    return list(qs.order_by("rank"))


def test_player_leaderboard_ranks_by_metric_with_stable_tiebreak() -> None:
    match, team1, _team2 = make_completed_match()
    p1 = make_player("Leader A")
    p2 = make_player("Leader B")
    p3 = make_player("Leader C")
    make_stat(match, p1, team1, runs=100, balls=60, out=True)
    make_stat(match, p2, team1, runs=50, balls=60, out=True)
    make_stat(match, p3, team1, runs=100, balls=60, out=True)

    # The full pipeline recomputes the career aggregates first, then the
    # leaderboards that read them (dependency-set ordering).
    recompute.recompute_match(match)

    rows = _leaderboard("runs")
    assert len(rows) == 3
    # Both 100-run players tie; the tiebreak is (value desc, player id asc).
    tied = [r for r in rows if r.value == Decimal("100")]
    assert [str(r.player_id) for r in tied] == sorted(str(r.player_id) for r in tied)
    assert rows[0].value == Decimal("100")
    assert rows[-1].player_id == p2.id
    assert rows[-1].value == Decimal("50")
    # Rank is dense and contiguous.
    assert [r.rank for r in rows] == [1, 2, 3]
    # Supporting carries the innings context.
    assert rows[0].supporting["matches_counted"] == 1


def test_leaderboard_bowling_metrics_ascending() -> None:
    match, team1, _team2 = make_completed_match()
    p1 = make_player("Bowl A")
    p2 = make_player("Bowl B")
    # p1 economy 4.0, p2 economy 6.0 → p1 ranks first (lower is better).
    make_stat(match, p1, team1, wickets=2, balls_bowled=60, runs_conceded=40)
    make_stat(match, p2, team1, wickets=1, balls_bowled=60, runs_conceded=60)

    recompute.recompute_match(match)

    rows = _leaderboard("economy")
    assert rows[0].player_id == p1.id
    assert rows[0].value == Decimal("4.000")
    assert rows[1].player_id == p2.id
    assert rows[1].value == Decimal("6.000")


def test_leaderboard_format_scope_is_isolated() -> None:
    odi, t1a, _t2a = make_completed_match(format_type="ODI")
    t20, t1b, _t2b = make_completed_match(format_type="T20I")
    p = make_player("Scope Player")
    make_stat(odi, p, t1a, runs=80, balls=60, out=True)
    make_stat(t20, p, t1b, runs=10, balls=20, out=True)

    recompute.recompute_match(odi)
    recompute.recompute_match(t20)

    odi_rows = _leaderboard("runs", scope="FORMAT", fmt="ODI")
    t20_rows = _leaderboard("runs", scope="FORMAT", fmt="T20I")
    assert len(odi_rows) == 1 and odi_rows[0].value == Decimal("80")
    assert len(t20_rows) == 1 and t20_rows[0].value == Decimal("10")


def test_leaderboard_recompute_is_digest_idempotent() -> None:
    match, team1, _team2 = make_completed_match()
    p = make_player("Idem")
    make_stat(match, p, team1, runs=40, balls=30, out=True)

    recompute.recompute_match(match)
    rows1 = _leaderboard("runs")
    stamp = rows1[0].computed_at

    recompute.recompute_match(match)
    rows2 = _leaderboard("runs")

    assert len(rows2) == len(rows1)
    assert rows2[0].computed_at == stamp  # untouched: digest unchanged


def test_dependency_keys_include_leaderboard_and_form() -> None:
    match, team1, _team2 = make_completed_match()
    p = make_player("Dep")
    make_stat(match, p, team1, runs=9, balls=6, out=True)

    keys = recompute.dependency_keys_for_match(match)

    assert "leaderboard:player:CAREER:-:-" in keys
    assert "leaderboard:player:FORMAT:-:T20I" in keys
    assert f"form:{p.id}:T20I" in keys
    assert f"form:{p.id}:-" in keys


def test_form_trend_windows_and_series() -> None:
    m1, t1a, _ = make_completed_match(format_type="T20I")
    m2, t1b, _ = make_completed_match(format_type="T20I")
    m3, t1c, _ = make_completed_match(format_type="T20I")
    p = make_player("Form")
    make_stat(m1, p, t1a, runs=20, balls=12, out=True)
    make_stat(m2, p, t1b, runs=30, balls=18, out=True)
    make_stat(m3, p, t1c, runs=50, balls=30, out=False)

    recompute.recompute_keys([f"form:{p.id}:T20I"])

    for window in (5, 10):
        row = FormTrend.objects.get(
            player_id=p.id, format_type="T20I", window_size=window, metric="runs"
        )
        assert row.value == Decimal("100")  # all three matches in the window
        assert len(row.series) == 3  # per-match values, chronological
        assert row.series[0]["runs"] == 20
        assert row.series[-1]["runs"] == 50

    avg = FormTrend.objects.get(
        player_id=p.id, format_type="T20I", window_size=5, metric="batting_avg"
    )
    assert avg.value == Decimal("50.000")  # 100 runs / 2 dismissals


def test_form_recompute_is_digest_idempotent() -> None:
    m1, t1a, _ = make_completed_match(format_type="T20I")
    p = make_player("FormIdem")
    make_stat(m1, p, t1a, runs=20, balls=12, out=True)

    recompute.recompute_keys([f"form:{p.id}:T20I"])
    stamp = FormTrend.objects.get(player_id=p.id, metric="runs", window_size=5).computed_at

    recompute.recompute_keys([f"form:{p.id}:T20I"])
    assert FormTrend.objects.get(player_id=p.id, metric="runs", window_size=5).computed_at == stamp
