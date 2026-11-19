"""Phase F Group 4: records catalogue + player compare.

Covers the on-demand records (no table — computed from the digest-validated
aggregate tables and the per-match source rows) and the compare endpoint's
validation (2–4 players, known ids) plus metric/delta logic.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from apps.core.enums import StatScope
from apps.statistics.services import compare, recompute, records

from .test_recompute import make_completed_match, make_player, make_stat

pytestmark = pytest.mark.django_db


def _recompute_career(players: list) -> None:
    keys = [f"career:{p.id}:{StatScope.CAREER.value}:-:-:M" for p in players]
    recompute.recompute_keys(keys)


def test_player_records_most_runs_and_highest_score() -> None:
    m1, t1a, _ = make_completed_match()
    m2, t1b, _ = make_completed_match()
    p1 = make_player("Record A")
    p2 = make_player("Record B")
    make_stat(m1, p1, t1a, runs=45, balls=30, out=False)  # 45*
    make_stat(m2, p1, t1b, runs=100, balls=80, out=True)
    make_stat(m1, p2, t1a, runs=30, balls=25, out=True)
    make_stat(m2, p2, t1b, runs=40, balls=35, out=True)
    _recompute_career([p1, p2])

    rec = records.player_records()

    assert rec["most_runs"]["player_id"] == str(p1.id)
    assert rec["most_runs"]["value"] == 145
    assert rec["highest_individual_score"]["player_id"] == str(p1.id)
    assert rec["highest_individual_score"]["value"] == 100
    assert rec["most_runs"]["detail"]  # human-readable, e.g. "145 runs"


def test_player_records_best_bowling_figures_prefers_wickets() -> None:
    m1, t1a, _ = make_completed_match()
    m2, t1b, _ = make_completed_match()
    p1 = make_player("Bowl Rec A")
    p2 = make_player("Bowl Rec B")
    make_stat(m1, p1, t1a, wickets=5, balls_bowled=24, runs_conceded=20)  # 5/20
    make_stat(m2, p2, t1b, wickets=4, balls_bowled=24, runs_conceded=10)  # 4/10
    _recompute_career([p1, p2])

    rec = records.player_records()

    # More wickets wins even though the 5-for conceded more runs.
    assert rec["best_bowling_figures"]["player_id"] == str(p1.id)
    assert rec["best_bowling_figures"]["detail"] == "5/20"
    assert rec["most_wickets"]["player_id"] == str(p1.id)


def test_team_records_highest_innings_total() -> None:
    m1, team1, team2 = make_completed_match()
    from apps.innings.models import Innings, InningsSummary

    inn1 = Innings.objects.create(
        match=m1, innings_number=1, batting_team=team1, bowling_team=team2, status="COMPLETED"
    )
    InningsSummary.objects.create(innings=inn1, runs=200, wickets=5, legal_balls=120)

    rec = records.team_records()

    assert rec["highest_team_total"]["team_id"] == str(team1.id)
    assert rec["highest_team_total"]["value"] == 200


def test_compare_rejects_wrong_player_counts() -> None:
    p = make_player("Solo")
    with pytest.raises(ValueError):
        compare.compare_players([])
    with pytest.raises(ValueError):
        compare.compare_players([str(p.id)])
    with pytest.raises(ValueError):
        compare.compare_players([str(p.id)] * 5)


def test_compare_rejects_unknown_player() -> None:
    p = make_player("Known")
    with pytest.raises(ValueError):
        compare.compare_players([str(uuid4()), str(p.id)])


def test_compare_returns_metric_values_with_deltas() -> None:
    m1, t1a, _ = make_completed_match()
    m2, t1b, _ = make_completed_match()
    p1 = make_player("Compare A")
    p2 = make_player("Compare B")
    make_stat(m1, p1, t1a, runs=60, balls=40, out=True)
    make_stat(m2, p1, t1b, runs=40, balls=30, out=True)
    make_stat(m1, p2, t1a, runs=25, balls=20, out=True)
    make_stat(m2, p2, t1b, runs=25, balls=20, out=True)
    _recompute_career([p1, p2])

    rows = compare.compare_players([str(p1.id), str(p2.id)], metric="runs")

    assert [r["player_id"] for r in rows] == [str(p1.id), str(p2.id)]
    assert rows[0]["values"]["runs"] == 100
    assert rows[1]["values"]["runs"] == 50
    # Deltas are relative to the first player: p2 is -50 runs behind.
    assert rows[1]["delta"]["runs"] == -50
    assert rows[0]["delta"]["runs"] == 0
