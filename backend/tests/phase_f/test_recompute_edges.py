"""Phase F Group 9: recompute pipeline edge branches (95% branch gate closure).

The main ``test_recompute`` suite covers the happy paths; this file closes the
edge branches that keep the ``.coveragerc-phasef`` 95% branch gate from
passing: SEASON/VENUE scopes executed end-to-end, empty-scope delete paths,
H2H TIE/DRAW/NO_RESULT/high-win classification, form result characters,
standings-with-no-matches, reconciliation diffs, event-handler guard clauses,
``_matches_in_scope`` validation and the distinct-match merge re-point.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from apps.competitions.models import Season, Series
from apps.core.enums import MatchStatus, StatScope
from apps.matches.models import MatchResult
from apps.scoring.types import Format, ResultType, TeamMatchLine
from apps.statistics.models import (
    HeadToHead,
    PlayerCareerStat,
    PlayerMatchStat,
    StatisticsWatermark,
    TeamStat,
    TournamentStandings,
)
from apps.statistics.services import recompute
from apps.venues.models import Venue

from ..phase_c.factories import make_match, make_match_teams, make_player, make_team
from .test_recompute import make_completed_match, make_stat

pytestmark = pytest.mark.django_db


def test_lines_from_non_list_payload_yields_no_lines() -> None:
    from apps.scoring.types import BattingLine

    assert recompute._lines_from({}, BattingLine) == ()


def test_dependency_keys_skip_stat_row_without_team() -> None:
    match = make_match(status=MatchStatus.COMPLETED)
    player = make_player("Teamless Batter")
    PlayerMatchStat.objects.create(match=match, player=player, team=None)
    keys = recompute.dependency_keys_for_match(match)
    assert not any(k.startswith("career:") for k in keys)


def test_career_season_and_venue_scope_recompute() -> None:
    season = Season.objects.create(label="2026")
    venue = Venue.objects.create(name="MCG")
    match, team1, _team2 = make_completed_match(season=season, venue=venue)
    player = make_player("Scope Batter")
    make_stat(match, player, team1, runs=30)

    recompute.recompute_keys(
        [
            f"career:{player.id}:{StatScope.SEASON}:{season.id}:-:{match.gender}",
            f"career:{player.id}:{StatScope.VENUE}:{venue.id}:-:{match.gender}",
        ]
    )
    assert PlayerCareerStat.objects.filter(
        player_id=player.id, scope_type=StatScope.SEASON, scope_ref_id=season.id
    ).exists()
    assert PlayerCareerStat.objects.filter(
        player_id=player.id, scope_type=StatScope.VENUE, scope_ref_id=venue.id
    ).exists()


def test_team_season_and_venue_scope_recompute() -> None:
    season = Season.objects.create(label="2026")
    venue = Venue.objects.create(name="Lord's")
    match, team1, _team2 = make_completed_match(season=season, venue=venue)
    player = make_player("Team Scope")
    make_stat(match, player, team1, runs=10)

    recompute.recompute_keys(
        [
            f"team:{team1.id}:{StatScope.SEASON}:{season.id}:-",
            f"team:{team1.id}:{StatScope.VENUE}:{venue.id}:-",
        ]
    )
    assert TeamStat.objects.filter(
        team_id=team1.id, scope_type=StatScope.SEASON, scope_ref_id=season.id
    ).exists()
    assert TeamStat.objects.filter(
        team_id=team1.id, scope_type=StatScope.VENUE, scope_ref_id=venue.id
    ).exists()


def test_team_key_with_no_matches_deletes_row() -> None:
    team = make_team("Empty XI")
    recompute.recompute_keys([f"team:{team.id}:{StatScope.CAREER}:-:-"])
    assert not TeamStat.objects.filter(team_id=team.id).exists()


def test_h2h_key_with_no_matches_deletes_row() -> None:
    a, b = make_team("H Alpha"), make_team("H Beta")
    low, high = sorted([str(a.id), str(b.id)])
    recompute.recompute_keys([f"h2h:{low}:{high}:-"])
    assert not HeadToHead.objects.filter(team_low_id=low, team_high_id=high).exists()


def test_h2h_classifies_tie_draw_no_result_and_high_wins() -> None:
    a, b = make_team("H Gamma"), make_team("H Delta")
    low, high = sorted([str(a.id), str(b.id)])
    high_team = a if str(a.id) == high else b
    outcomes = [
        ("TIE", None),
        ("DRAW", None),
        ("NO_RESULT", None),
        ("WIN", high_team),
    ]
    for result_type, winner in outcomes:
        match = make_match(
            format_type="T20I",
            status=MatchStatus.COMPLETED,
            source_local_date=date(2026, 1, 1),
        )
        make_match_teams(match, a, b)
        MatchResult.objects.create(match=match, result_type=result_type, winner_team=winner)

    recompute.recompute_keys([f"h2h:{low}:{high}:-"])
    row = HeadToHead.objects.get(team_low_id=low, team_high_id=high)
    assert (row.ties, row.draws, row.no_results) == (1, 1, 1)
    # The WIN went to the canonical *high* team → high_wins, not low_wins.
    assert (row.low_wins, row.high_wins) == (0, 1)


def test_result_char_draw_and_unresolved() -> None:
    draw = TeamMatchLine(
        rule_version="1",
        match_id="m1",
        team_id="t1",
        format=Format.T20I,
        result_type=ResultType.DRAW,
    )
    assert recompute._result_char(draw) == "D"
    unresolved = TeamMatchLine(
        rule_version="1", match_id="m2", team_id="t2", format=Format.T20I, result_type=None
    )
    assert recompute._result_char(unresolved) == "N"


def test_standings_key_with_no_matches_deletes_row() -> None:
    series = Series.objects.create(name="Empty Series", is_tournament=True)
    recompute.recompute_keys([f"standings:{series.id}:-"])
    assert not TournamentStandings.objects.filter(series_id=series.id).exists()


def test_reconcile_diff_missing_team_and_nrr_rounding() -> None:
    ours = {
        "11111111-1111-1111-1111-111111111111": {"played": 1},
        "22222222-2222-2222-2222-222222222222": {"played": 1},
    }
    provider = {"22222222-2222-2222-2222-222222222222": {"played": 1}}
    diffs = recompute._diff_standings(ours, provider)
    assert "11111111-1111-1111-1111-111111111111" in diffs

    nrr_diffs = recompute._diff_standings({"a": {"nrr": 1.23456}}, {"a": {"nrr": 1.2}})
    assert "a" in nrr_diffs


def test_recompute_from_event_ignores_bad_payloads() -> None:
    recompute._recompute_from_event({}, correction=False)
    recompute._recompute_from_event({"match_id": str(uuid4())}, correction=False)


def test_matches_in_scope_validation_and_filters() -> None:
    with pytest.raises(ValueError, match="requires --ids"):
        recompute._matches_in_scope("player")
    with pytest.raises(ValueError, match="requires --ids"):
        recompute._matches_in_scope("team")
    with pytest.raises(ValueError, match="unknown scope"):
        recompute._matches_in_scope("bogus")

    series = Series.objects.create(name="Scope Series")
    assert recompute._matches_in_scope("series", ids=[str(series.id)]) == []

    match, team1, _team2 = make_completed_match()
    player = make_player("Scope Player")
    make_stat(match, player, team1, runs=5)
    assert recompute._matches_in_scope("player", ids=[str(player.id)]) == [match]
    assert isinstance(recompute._matches_in_scope("all", since="2026-01-01"), list)


def test_recompute_match_with_empty_dependency_set() -> None:
    match = make_match(status=MatchStatus.COMPLETED)  # no teams, no stats
    affected = recompute.recompute_match(match)
    assert affected == []
    assert StatisticsWatermark.objects.filter(match_id=match.id).exists()


def test_player_merged_ignores_missing_ids() -> None:
    recompute._on_player_merged({})
    recompute._on_player_merged(
        {"canonical_player_id": str(uuid4()), "merged_player_id": str(uuid4())}
    )


def test_merge_repoints_distinct_matches() -> None:
    match_a, team1, _team2 = make_completed_match()
    match_b, team1, _team2 = make_completed_match()
    winner = make_player("Winner W")
    loser = make_player("Loser L")
    make_stat(match_a, winner, team1, runs=10)
    make_stat(match_b, loser, team1, runs=20)

    recompute._repoint_source_rows(str(winner.id), str(loser.id))
    assert PlayerMatchStat.objects.filter(player_id=loser.id).count() == 0
    assert PlayerMatchStat.objects.filter(player_id=winner.id, match_id=match_b.id).exists()
