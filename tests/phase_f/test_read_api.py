"""Phase F Group 7: the statistics/analytics read API.

Every Phase F endpoint under ``/api/v1/`` with its envelope, cache and
validation contract: player statistics/form/compare, team statistics, match
statistics, series points-table + stats, the three leaderboards, team/player
records, head-to-head, cross-entity search, merged-player 301 redirects, and
the query-count + no-``delivery`` guards (Phase F reads must never scan ball
data).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.competitions.models import Series
from apps.matches.models import MatchResult
from apps.players.models import PlayerMerge
from apps.statistics.services import recompute

from ..phase_c.factories import make_match, make_match_teams, make_player, make_team

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _series(name: str = "World Cup", **overrides: object) -> Series:
    return Series.objects.create(
        name=name,
        series_type="tournament",
        is_tournament=True,
        **overrides,
    )


def _series_result(
    series: Series,
    team1: object,
    team2: object,
    *,
    start: date,
    winner: object,
) -> object:
    match = make_match(
        start_date=start,
        format_type="ODI",
        gender="M",
        series=series,
        status="COMPLETED",
    )
    make_match_teams(match, team1, team2)
    MatchResult.objects.create(match=match, result_type="WIN", winner_team=winner)
    return match


def _player_match(format_type: str = "T20I", **overrides: object):
    from tests.phase_f.test_recompute import make_completed_match

    return make_completed_match(format_type=format_type, **overrides)


def _stat(match, player, team, **kwargs):
    from tests.phase_f.test_recompute import make_stat

    return make_stat(match, player, team, **kwargs)


# --------------------------------------------------------------------------- #
# Player statistics / form / compare
# --------------------------------------------------------------------------- #


def test_player_statistics_career_envelope(api_client: object) -> None:
    match, team1, _team2 = _player_match()
    p = make_player("Career Stats")
    _stat(
        match, p, team1, runs=80, balls=60, out=True, wickets=2, balls_bowled=60, runs_conceded=40
    )
    recompute.recompute_match(match)

    response = api_client.get(f"/api/v1/players/{p.id}/statistics")
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["official_source"] == "computed"
    assert body["meta"]["scope"] == "CAREER"
    batting = body["data"]["official"]["batting"]
    assert batting["runs"] == 80
    assert batting["innings"] == 1
    assert body["data"]["official"]["bowling"]["wickets"] == 2
    assert body["data"]["player"]["name"] == "Career Stats"


def test_player_statistics_format_scope(api_client: object) -> None:
    odi, t1a, _ = _player_match("ODI")
    t20, t1b, _ = _player_match("T20I")
    p = make_player("Format Stats")
    _stat(odi, p, t1a, runs=40, balls=30, out=True)
    _stat(t20, p, t1b, runs=10, balls=12, out=True)
    recompute.recompute_match(odi)
    recompute.recompute_match(t20)

    response = api_client.get(f"/api/v1/players/{p.id}/statistics?scope=FORMAT&format=ODI")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["scope"] == "FORMAT"
    assert body["data"]["format"] == "ODI"
    assert body["data"]["official"]["batting"]["runs"] == 40


def test_player_form_endpoint(api_client: object) -> None:
    m1, t1a, _ = _player_match()
    m2, t1b, _ = _player_match()
    p = make_player("Form Read")
    _stat(m1, p, t1a, runs=20, balls=12, out=True)
    _stat(m2, p, t1b, runs=30, balls=18, out=True)
    recompute.recompute_match(m1)
    recompute.recompute_match(m2)

    response = api_client.get(f"/api/v1/players/{p.id}/form")
    assert response.status_code == 200
    body = response.json()
    runs = [w for w in body["data"]["windows"] if w["metric"] == "runs" and w["window"] == 5]
    assert runs and runs[0]["value"] == 50.0
    assert runs[0]["series"]  # per-match chronological series present


def test_player_compare_endpoint(api_client: object) -> None:
    m, t1, _ = _player_match()
    p1 = make_player("Compare One")
    p2 = make_player("Compare Two")
    _stat(m, p1, t1, runs=60, balls=40, out=True)
    _stat(m, p2, t1, runs=30, balls=20, out=True)
    recompute.recompute_match(m)

    response = api_client.get(f"/api/v1/players/compare?ids={p1.id}&ids={p2.id}&metric=runs")
    assert response.status_code == 200
    rows = response.json()["data"]
    assert [r["player_id"] for r in rows] == [str(p1.id), str(p2.id)]
    assert rows[0]["values"]["runs"] == 60
    assert rows[1]["delta"]["runs"] == -30


def test_player_compare_rejects_bad_counts(api_client: object) -> None:
    p = make_player("Solo Compare")
    response = api_client.get(f"/api/v1/players/compare?ids={p.id}")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_query"


def test_merged_player_statistics_answers_301(api_client: object) -> None:
    canonical = make_player("Canonical")
    merged = make_player("Merged Away")
    PlayerMerge.objects.create(canonical_player=canonical, merged_player=merged)

    for path in (
        f"/api/v1/players/{merged.id}/statistics",
        f"/api/v1/players/{merged.id}/form",
        f"/api/v1/players/{merged.id}/rankings",
    ):
        response = api_client.get(path)
        assert response.status_code == 301
        assert response["Location"] == f"/api/v1/players/{canonical.id}/"


# --------------------------------------------------------------------------- #
# Team / match / series
# --------------------------------------------------------------------------- #


def test_team_statistics_endpoint(api_client: object) -> None:
    match, team1, _team2 = _player_match()
    p = make_player("Team Agg")
    _stat(match, p, team1, runs=5, wickets=1)
    recompute.recompute_match(match)

    response = api_client.get(f"/api/v1/teams/{team1.id}/statistics")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["team"]["name"] == "India"
    assert body["data"]["official"]["matches"] == 1
    assert body["data"]["official"]["won"] == 1


def test_match_statistics_endpoint(api_client: object) -> None:
    match, team1, _team2 = _player_match()
    p1 = make_player("Match Bat")
    p2 = make_player("Match Bowl")
    _stat(match, p1, team1, runs=55, balls=40, out=True)
    _stat(match, p2, team1, runs=0, wickets=3, balls_bowled=24, runs_conceded=12)
    recompute.recompute_match(match)

    response = api_client.get(f"/api/v1/matches/{match.id}/statistics")
    assert response.status_code == 200
    rows = response.json()["data"]["players"]
    by_name = {r["player"]["name"]: r for r in rows}
    assert by_name["Match Bat"]["batting"]["runs"] == 55
    assert by_name["Match Bowl"]["bowling"]["wickets"] == 3


def test_series_points_table_endpoint(api_client: object) -> None:
    series = _series()
    india, australia = make_team("India"), make_team("Australia")
    _series_result(series, india, australia, start=date(2026, 2, 1), winner=india)
    recompute.recompute_keys([f"standings:{series.id}:-"])

    response = api_client.get(f"/api/v1/series/{series.id}/points-table")
    assert response.status_code == 200
    body = response.json()
    rows = body["data"]["standings"]
    assert len(rows) == 2
    india_row = next(r for r in rows if r["team"]["name"] == "India")
    assert india_row["won"] == 1
    assert india_row["points"] == 2  # ODI default: win = 2 points


def test_series_stats_endpoint(api_client: object) -> None:
    series = _series()
    match, team1, _team2 = _player_match(series=series)
    p = make_player("Series Bat")
    _stat(match, p, team1, runs=70, balls=50, out=True)
    recompute.recompute_match(match)

    response = api_client.get(f"/api/v1/series/{series.id}/stats?metric=runs")
    assert response.status_code == 200
    rows = response.json()["data"]
    assert rows and rows[0]["player"]["name"] == "Series Bat"
    assert Decimal(str(rows[0]["value"])) == Decimal("70.000")


# --------------------------------------------------------------------------- #
# Leaderboards / records / H2H
# --------------------------------------------------------------------------- #


def _leaderboard_seed() -> object:
    match, team1, _team2 = _player_match()
    p1 = make_player("Lb Bat A")
    p2 = make_player("Lb Bat B")
    _stat(match, p1, team1, runs=100, balls=60, out=True)
    _stat(match, p2, team1, runs=40, balls=30, out=True)
    recompute.recompute_match(match)
    return p1, p2


def test_batting_leaderboard_endpoint(api_client: object) -> None:
    p1, _p2 = _leaderboard_seed()
    response = api_client.get("/api/v1/statistics/batting?metric=runs")
    assert response.status_code == 200
    rows = response.json()["data"]
    assert rows[0]["player"]["id"] == str(p1.id)
    assert Decimal(str(rows[0]["value"])) == Decimal("100.000")


def test_bowling_and_fielding_leaderboards(api_client: object) -> None:
    match, team1, _team2 = _player_match()
    p = make_player("Lb Bowl")
    _stat(match, p, team1, wickets=4, balls_bowled=24, runs_conceded=20)
    recompute.recompute_match(match)

    bowling = api_client.get("/api/v1/statistics/bowling?metric=wickets").json()["data"]
    assert bowling[0]["player"]["id"] == str(p.id)

    # Fielding has no per-match source yet: honest empty list, never an error.
    fielding = api_client.get("/api/v1/statistics/fielding").json()["data"]
    assert fielding == []


def test_team_records_endpoint(api_client: object) -> None:
    _leaderboard_seed()
    response = api_client.get("/api/v1/statistics/teams")
    assert response.status_code == 200
    assert response.json()["data"]  # non-empty catalogue


def test_records_players_and_teams_endpoints(api_client: object) -> None:
    _leaderboard_seed()
    players = api_client.get("/api/v1/records/players").json()["data"]
    assert players["most_runs"]  # the seeded batter holds it
    teams = api_client.get("/api/v1/records/teams").json()["data"]
    assert teams  # non-empty catalogue


def test_head_to_head_endpoint(api_client: object) -> None:
    match, team1, team2 = _player_match()
    p = make_player("H2H")
    _stat(match, p, team1, runs=1)
    recompute.recompute_match(match)

    response = api_client.get(f"/api/v1/head-to-head?team1={team1.id}&team2={team2.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["team1"]["name"] == "India"
    assert body["data"]["matches"] == 1
    assert body["data"]["team1_wins"] == 1


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #


def test_search_endpoint(api_client: object) -> None:
    make_player("Virat Kohli")
    make_team("India")

    response = api_client.get("/api/v1/search?q=virat")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]
    assert body["data"][0]["type"] == "player"
    assert body["data"][0]["name"] == "Virat Kohli"
    assert body["meta"]["query"] == "virat"

    bad = api_client.get("/api/v1/search?q=x")
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_query"


# --------------------------------------------------------------------------- #
# Guards: query counts, no delivery scans
# --------------------------------------------------------------------------- #


def _assert_no_delivery_scan(response: object, queries: list[object]) -> None:
    assert response.status_code == 200
    sql = " ".join(str(q) for q in queries)
    assert "delivery" not in sql  # Phase F reads never touch ball data


def test_guards_query_counts_and_no_delivery(api_client: object) -> None:
    match, team1, team2 = _player_match()
    p = make_player("Guard Bat")
    _stat(match, p, team1, runs=10, wickets=1)
    recompute.recompute_match(match)

    cases = (
        "/api/v1/statistics/batting?metric=runs",
        "/api/v1/records/players",
        f"/api/v1/head-to-head?team1={team1.id}&team2={team2.id}",
        f"/api/v1/players/{p.id}/statistics",
    )
    for path in cases:
        with CaptureQueriesContext(connection) as queries:
            response = api_client.get(path)
        assert len(queries) <= 6, f"{path}: {len(queries)} queries"
        _assert_no_delivery_scan(response, queries)
