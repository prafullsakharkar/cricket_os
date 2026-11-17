"""Snapshot builder (Task 3): canonical match statistics -> frozen
``FantasyMatchSnapshot`` with coverage flags, stable digest, and a
compressed JSON payload that round-trips.

Data model: the builder reads the same canonical rows the Phase F aggregates
consume — ``Match``, ``MatchResult``, ``MatchPlayer``, ``PlayerMatchStat``
(batting/bowling JSON lists, fielding JSON) and ``MatchAward`` — never
``delivery``. Fielding counts are read from the fielding JSON when present,
otherwise derived from dismissal credits in the batting lines (caught ->
fielder1 gets a catch; stumped -> fielder1 a stumping; run_out -> each listed
fielder a run-out). A summary-only match (any ``is_partial`` stat row) yields
reduced coverage and the snapshot is flagged partial — never guessed.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.core.enums import FormatType, MatchStatus, ResultType
from apps.fantasy.engine import FantasyMatchSnapshot, MatchContext
from apps.fantasy.services.snapshot import (
    build_snapshot,
    snapshot_digest,
    snapshot_from_payload,
    snapshot_payload,
)
from apps.matches.models import Match, MatchAward, MatchPlayer, MatchResult
from apps.statistics.models import PlayerMatchStat
from tests.phase_c.factories import make_match, make_match_teams, make_player, make_team

pytestmark = pytest.mark.django_db


def _bat(
    runs: int,
    balls: int = 0,
    fours: int = 0,
    sixes: int = 0,
    out: bool = False,
    dismissal_type: str | None = None,
    fielder1_id: str | None = None,
    fielder2_id: str | None = None,
    bowler_id: str | None = None,
    retirement_type: str | None = None,
) -> dict:
    line: dict = {
        "rule_version": "test",
        "player_id": "p",
        "did_bat": True,
        "runs": runs,
        "balls": balls,
        "fours": fours,
        "sixes": sixes,
        "ones": 0,
        "twos": 0,
        "threes": 0,
        "fives": 0,
        "dots": 0,
        "out": out,
        "dismissal_type": dismissal_type,
        "bowler_id": bowler_id,
        "fielder1_id": fielder1_id,
        "fielder2_id": fielder2_id,
        "fielder3_id": None,
        "retirement_type": retirement_type,
        "strike_rate": None,
    }
    return line


def _bowl(
    legal_balls: int,
    runs_conceded: int,
    wickets: int = 0,
    maidens: int = 0,
    dots: int = 0,
) -> dict:
    return {
        "rule_version": "test",
        "player_id": "p",
        "legal_balls": legal_balls,
        "runs_conceded": runs_conceded,
        "wickets": wickets,
        "maidens": maidens,
        "wides": 0,
        "noballs": 0,
        "dots": dots,
        "overs_display": f"{legal_balls // 6}.{legal_balls % 6}",
        "economy": None,
    }


def _make_completed_match() -> tuple[Match, object, object]:
    match = make_match(
        format_type=FormatType.T20I,
        status=MatchStatus.COMPLETED,
        end_time_utc=timezone.now(),
        result_type=ResultType.WIN,
        series_id=None,
    )
    team1 = make_team("India")
    team2 = make_team("Australia")
    make_match_teams(match, team1, team2)
    MatchResult.objects.create(match=match, result_type="WIN", winner_team=team1)
    return match, team1, team2


def _add_player(
    match: Match,
    team: object,
    *,
    batsman: list[dict] | None = None,
    bowler: list[dict] | None = None,
    fielding: dict | None = None,
    is_playing_xi: bool = True,
    is_substitute: bool = False,
    is_partial: bool = False,
) -> tuple[object, PlayerMatchStat]:
    player = make_player()
    MatchPlayer.objects.create(
        match=match,
        player=player,
        team_id=str(team.id),  # type: ignore[attr-defined]
        is_playing_xi=is_playing_xi,
        is_substitute=is_substitute,
    )
    stat = PlayerMatchStat.objects.create(
        match=match,
        player=player,
        team_id=str(team.id),  # type: ignore[attr-defined]
        did_bat=bool(batsman),
        did_bowl=bool(bowler),
        did_field=True,
        is_playing_xi=is_playing_xi,
        batting=batsman or [],
        bowling=bowler or [],
        fielding=fielding or {},
        is_partial=is_partial,
    )
    return player, stat


def test_full_ball_by_ball_match_snapshot_coverage() -> None:
    match, team1, team2 = _make_completed_match()
    _add_player(
        match,
        team1,
        batsman=[
            _bat(
                runs=58,
                balls=42,
                fours=5,
                sixes=2,
                out=True,
                dismissal_type="CAUGHT",
                fielder1_id="p-catcher",
            )
        ],
    )
    bowler_player = make_player()
    MatchPlayer.objects.create(
        match=match, player=bowler_player, team_id=str(team2.id), is_playing_xi=True
    )
    PlayerMatchStat.objects.create(
        match=match,
        player=bowler_player,
        team_id=str(team2.id),
        did_bat=True,
        did_bowl=True,
        did_field=True,
        is_playing_xi=True,
        batting=[
            _bat(
                runs=20,
                balls=18,
                out=True,
                dismissal_type="BOWLED",
                bowler_id=str(bowler_player.id),
            )
        ],
        bowling=[_bowl(legal_balls=24, runs_conceded=22, wickets=3, maidens=1, dots=8)],
        fielding={},
        is_partial=False,
    )

    snapshot = build_snapshot(match)

    assert isinstance(snapshot, FantasyMatchSnapshot)
    assert snapshot.match.match_id == str(match.id)
    assert snapshot.coverage.has_ball_data is True
    assert snapshot.coverage.has_dismissal_kinds is True
    assert snapshot.coverage.has_fielding_data is True
    assert snapshot.digest  # canonical digest filled
    assert len(snapshot.player_lines) == 2


def test_summary_only_match_reduced_coverage() -> None:
    match, team1, _ = _make_completed_match()
    _add_player(
        match,
        team1,
        batsman=[_bat(runs=30, balls=24, out=True, dismissal_type="CAUGHT")],
        is_partial=True,  # innings came from a summary-only import
    )
    snapshot = build_snapshot(match)
    # No ball-by-ball data -> ball-dependent metrics unavailable.
    assert snapshot.coverage.has_ball_data is False
    # Dismissal kinds are present in the summary...
    assert snapshot.coverage.has_dismissal_kinds is True
    # ...but no fielder credits -> fielding metrics unavailable (never guessed).
    assert snapshot.coverage.has_fielding_data is False


def test_digest_stable_across_builds() -> None:
    match, team1, _ = _make_completed_match()
    _add_player(
        match,
        team1,
        batsman=[
            _bat(
                runs=58,
                balls=42,
                fours=5,
                out=True,
                dismissal_type="CAUGHT",
                fielder1_id="p-catcher",
            )
        ],
    )
    first = build_snapshot(match)
    second = build_snapshot(match)
    assert first.digest == second.digest
    assert snapshot_digest(first) == first.digest


def test_snapshot_payload_round_trips() -> None:
    match, team1, team2 = _make_completed_match()
    _batter, _ = _add_player(
        match,
        team1,
        batsman=[
            _bat(
                runs=58,
                balls=42,
                fours=5,
                sixes=2,
                out=True,
                dismissal_type="CAUGHT",
                fielder1_id="p-catcher",
            )
        ],
    )
    _add_player(
        match,
        team2,
        bowler=[_bowl(legal_balls=24, runs_conceded=22, wickets=3)],
    )
    snapshot = build_snapshot(match)
    payload = snapshot_payload(snapshot)
    restored = snapshot_from_payload(payload)
    assert restored.digest == snapshot.digest
    assert restored.match.match_id == snapshot.match.match_id
    assert {line.player_id for line in restored.player_lines} == {
        line.player_id for line in snapshot.player_lines
    }


def test_playing_xi_substitutes_and_awards_captured() -> None:
    match, team1, team2 = _make_completed_match()
    xi, _ = _add_player(match, team1, batsman=[_bat(runs=10)])
    sub, _ = _add_player(match, team2, is_substitute=True)
    pom, _ = _add_player(match, team1, batsman=[_bat(runs=70)])
    MatchAward.objects.create(match=match, player=pom, award_type="player_of_match")

    snapshot = build_snapshot(match)
    lines = {line.player_id: line for line in snapshot.player_lines}
    assert lines[str(xi.id)].is_playing_xi is True
    assert lines[str(sub.id)].is_substitute is True
    assert lines[str(pom.id)].player_of_match is True


def test_dismissal_kinds_normalized_to_engine_vocabulary() -> None:
    match, team1, _ = _make_completed_match()
    _add_player(
        match,
        team1,
        batsman=[_bat(runs=0, balls=1, out=True, dismissal_type="BOWLED", bowler_id="p-bowler")],
    )
    snapshot = build_snapshot(match)
    line = snapshot.player_lines[0]
    assert line.dismissal_kind == "bowled"
    assert line.runs == 0


def test_fielding_derived_from_dismissal_credits() -> None:
    match, team1, _ = _make_completed_match()
    catcher, _ = _add_player(match, team1, batsman=[_bat(runs=5, balls=6)])
    stumper, _ = _add_player(match, team1, batsman=[_bat(runs=5, balls=6)])
    fielder, _ = _add_player(match, team1, batsman=[_bat(runs=5, balls=6)])
    # Three dismissed batters credit the fielders.
    for _pid, dt, f1, f2 in (
        ("b1", "CAUGHT", str(catcher.id), None),
        ("b2", "STUMPED", str(stumper.id), None),
        ("b3", "RUN_OUT", str(fielder.id), str(fielder.id)),
    ):
        _add_player(
            match,
            team1,
            batsman=[
                _bat(runs=1, balls=2, out=True, dismissal_type=dt, fielder1_id=f1, fielder2_id=f2)
            ],
        )
    snapshot = build_snapshot(match)
    lines = {line.player_id: line for line in snapshot.player_lines}
    assert lines[str(catcher.id)].catches == 1
    assert lines[str(stumper.id)].stumpings == 1
    # The run-out credits both listed fielders to the same player -> 2.
    assert lines[str(fielder.id)].run_outs_total == 2


def test_bowled_lbw_wickets_credited_to_bowler() -> None:
    match, team1, _ = _make_completed_match()
    bowler, _ = _add_player(
        match, team1, bowler=[_bowl(legal_balls=24, runs_conceded=20, wickets=2)]
    )
    _add_player(
        match,
        team1,
        batsman=[
            _bat(runs=0, balls=1, out=True, dismissal_type="BOWLED", bowler_id=str(bowler.id))
        ],
    )
    _add_player(
        match,
        team1,
        batsman=[_bat(runs=2, balls=5, out=True, dismissal_type="LBW", bowler_id=str(bowler.id))],
    )
    snapshot = build_snapshot(match)
    lines = {line.player_id: line for line in snapshot.player_lines}
    assert lines[str(bowler.id)].bowled_lbw_wickets == 2
    assert lines[str(bowler.id)].wickets == 2


def test_super_over_and_dls_flags_captured() -> None:
    team1 = make_team("India")
    team2 = make_team("Australia")
    match = make_match(
        format_type=FormatType.T20I,
        status=MatchStatus.COMPLETED,
        end_time_utc=timezone.now(),
        result_type=ResultType.SUPER_OVER_TIE_BREAK,
        is_dls_applied=False,
    )
    make_match_teams(match, team1, team2)
    MatchResult.objects.create(
        match=match, result_type="SUPER_OVER_TIE_BREAK", is_super_over=True, is_dls=False
    )
    _add_player(match, team1, batsman=[_bat(runs=10)])
    snapshot = build_snapshot(match)
    assert snapshot.super_over_played is True

    dls_match, team1b, _ = _make_completed_match()
    dls_match.is_dls_applied = True
    dls_match.save(update_fields=["is_dls_applied"])
    _add_player(dls_match, team1b, batsman=[_bat(runs=10)])
    dls_snapshot = build_snapshot(dls_match)
    assert dls_snapshot.reduced_overs is True


def test_build_snapshot_query_count_is_bounded() -> None:
    """The selector gathers everything in four indexed queries (no N+1)."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    match, team1, team2 = _make_completed_match()
    for _ in range(3):
        _add_player(match, team1, batsman=[_bat(runs=10, balls=6)])
    _add_player(match, team2, bowler=[_bowl(legal_balls=6, runs_conceded=8)])

    with CaptureQueriesContext(connection) as ctx:
        build_snapshot(match)
    assert len(ctx.captured_queries) <= 5, len(ctx.captured_queries)


def test_match_context_carries_format_and_result_type() -> None:
    match, team1, _ = _make_completed_match()
    _add_player(match, team1, batsman=[_bat(runs=10)])
    snapshot = build_snapshot(match)
    assert isinstance(snapshot.match, MatchContext)
    assert snapshot.match.match_id == str(match.id)
    assert snapshot.match.format == FormatType.T20I
    assert snapshot.match.result_type == "WIN"


def test_caught_and_bowled_credits_the_bowler() -> None:
    """A CAUGHT_AND_BOWLED dismissal without an explicit fielder credits the
    bowler (the bowler IS the catcher)."""
    match, team1, _ = _make_completed_match()
    bowler, _ = _add_player(match, team1, bowler=[_bowl(legal_balls=6, runs_conceded=8)])
    _add_player(
        match,
        team1,
        batsman=[
            _bat(
                runs=1,
                balls=2,
                out=True,
                dismissal_type="CAUGHT_AND_BOWLED",
                bowler_id=str(bowler.id),
            )
        ],
    )
    snapshot = build_snapshot(match)
    lines = {line.player_id: line for line in snapshot.player_lines}
    assert lines[str(bowler.id)].catches == 1


def test_fielding_json_takes_precedence_over_derived() -> None:
    """A stat row's fielding JSON (catches/stumpings/run-outs incl. direct/
    indirect attribution) is authoritative when present."""
    match, team1, _ = _make_completed_match()
    fielder, _ = _add_player(
        match,
        team1,
        batsman=[_bat(runs=10, balls=8)],
        fielding={
            "catches": 2,
            "stumpings": 1,
            "run_outs": 1,
            "run_outs_direct": 1,
            "run_outs_indirect": 0,
        },
    )
    snapshot = build_snapshot(match)
    line = next(line for line in snapshot.player_lines if line.player_id == str(fielder.id))
    assert line.catches == 2
    assert line.stumpings == 1
    assert line.run_outs_total == 1
    assert line.run_outs_direct == 1
    assert line.run_outs_indirect == 0
    # The JSON presence flips the has_fielding_data coverage flag.
    assert snapshot.coverage.has_fielding_data is True


def test_dismissals_without_credited_fielders_are_skipped() -> None:
    """A stumped/run-out dismissal that names no fielder contributes nothing
    to the derived fielding counts (no player to credit)."""
    match, team1, _ = _make_completed_match()
    _add_player(
        match,
        team1,
        batsman=[_bat(runs=1, balls=2, out=True, dismissal_type="STUMPED", fielder1_id=None)],
    )
    _add_player(
        match,
        team1,
        batsman=[
            _bat(
                runs=1,
                balls=2,
                out=True,
                dismissal_type="RUN_OUT",
                fielder1_id=None,
                fielder2_id=None,
            )
        ],
    )
    snapshot = build_snapshot(match)
    # No player accumulates any fielding credit.
    assert all(
        line.catches == 0 and line.stumpings == 0 and line.run_outs_total == 0
        for line in snapshot.player_lines
    )


def test_build_snapshot_raises_for_unknown_match() -> None:
    import uuid

    with pytest.raises(ValueError):
        build_snapshot(uuid.uuid4())


def test_snapshot_coverage_bool_truthiness() -> None:
    """``SnapshotCoverage.__bool__`` is True for any real snapshot (used to
    reject degenerate empty snapshots)."""
    match, team1, _ = _make_completed_match()
    _add_player(match, team1, batsman=[_bat(runs=10, balls=6)])
    snapshot = build_snapshot(match)
    assert bool(snapshot.coverage) is True


def test_snapshot_digest_from_matches_snapshot_digest() -> None:
    from apps.fantasy.services.snapshot import snapshot_digest_from

    match, team1, _ = _make_completed_match()
    _add_player(match, team1, batsman=[_bat(runs=10, balls=6)])
    snapshot = build_snapshot(match)
    assert snapshot_digest_from(snapshot) == snapshot_digest(snapshot)


def test_completed_match_without_result_builds_snapshot() -> None:
    """A completed match whose result row is missing still snapshots (the
    result is optional in the selector; policies decide points)."""
    match = make_match(
        format_type=FormatType.T20I,
        status=MatchStatus.COMPLETED,
        end_time_utc=timezone.now(),
        series_id=None,
    )
    team1 = make_team("India")
    team2 = make_team("Australia")
    make_match_teams(match, team1, team2)
    _add_player(match, team1, batsman=[_bat(runs=10, balls=6)])
    snapshot = build_snapshot(match)
    assert snapshot.match.result_type is None
