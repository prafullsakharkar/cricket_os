"""Golden cases for every rule type, with the arithmetic written out.

Each case is hand-computed (Decimal, ROUND_HALF_UP to 2dp). Qualifier edges
are covered: exactly at a threshold, one ball below min_balls, tier
boundaries under both tier modes. The independent oracle
(``test_engine_oracle.py``) re-checks the same fixtures with a deliberately
different implementation.
"""

from __future__ import annotations

from decimal import Decimal

from apps.fantasy.engine import (
    FantasyMatchSnapshot,
    MatchContext,
    PlayerLine,
    RulesetPolicies,
    RulesetSpec,
    SnapshotCoverage,
    compute,
    point_rule,
)

FULL_COVERAGE = SnapshotCoverage(
    has_ball_data=True, has_dismissal_kinds=True, has_fielder_credit_types=True
)
NO_BALL_COVERAGE = SnapshotCoverage(
    has_ball_data=False, has_dismissal_kinds=False, has_fielder_credit_types=False
)


def _snapshot(
    *lines: PlayerLine,
    coverage: SnapshotCoverage = FULL_COVERAGE,
    result_type: str = "NORMAL",
) -> FantasyMatchSnapshot:
    return FantasyMatchSnapshot(
        match=MatchContext(match_id="m1", format="T20", result_type=result_type),
        coverage=coverage,
        player_lines=tuple(lines),
    )


def _bat(
    player_id: str,
    runs: int = 0,
    balls: int | None = None,
    fours: int = 0,
    sixes: int = 0,
    dismissal: str | None = None,
    retired_hurt: bool = False,
) -> PlayerLine:
    return PlayerLine(
        player_id=player_id,
        is_playing_xi=True,
        did_bat=True,
        runs=runs,
        balls_faced=balls,
        fours=fours,
        sixes=sixes,
        dismissal_kind=dismissal,
        retired_hurt=retired_hurt,
    )


def _bowl(
    player_id: str,
    wickets: int = 0,
    balls: int | None = None,
    runs_conceded: int | None = None,
    maidens: int | None = None,
    dots: int | None = None,
    bowled_lbw: int = 0,
    hat_trick: bool = False,
) -> PlayerLine:
    return PlayerLine(
        player_id=player_id,
        is_playing_xi=True,
        did_bowl=True,
        wickets=wickets,
        balls_bowled=balls,
        runs_conceded=runs_conceded,
        maidens=maidens,
        dots_bowled=dots,
        bowled_lbw_wickets=bowled_lbw,
        hat_trick=hat_trick,
    )


def _field(
    player_id: str,
    catches: int = 0,
    stumpings: int = 0,
    run_outs: int = 0,
    direct: int | None = None,
    indirect: int | None = None,
) -> PlayerLine:
    return PlayerLine(
        player_id=player_id,
        is_playing_xi=True,
        did_field=True,
        catches=catches,
        stumpings=stumpings,
        run_outs_total=run_outs,
        run_outs_direct=direct,
        run_outs_indirect=indirect,
    )


def _result(ruleset: RulesetSpec, snapshot: FantasyMatchSnapshot):
    return compute(ruleset, snapshot)


def _player(result, player_id: str):
    for p in result.players:
        if p.player_id == player_id:
            return p
    raise AssertionError(f"player {player_id} missing from result")


# --------------------------------------------------------------------------- #
# per_unit
# --------------------------------------------------------------------------- #


def test_runs_per_unit_golden() -> None:
    # 127 runs @ 1.00/run = 127.00 ; 0 runs -> no component.
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(point_rule("runs", "per_unit", "1.00", "batting"),),
    )
    result = _result(ruleset, _snapshot(_bat("a", runs=127), _bat("b", runs=0)))
    p = _player(result, "a")
    assert p.total == Decimal("127.00")
    assert [(c.metric, c.points) for c in p.components] == [("runs", Decimal("127.00"))]
    assert _player(result, "b").total == Decimal("0.00")
    assert _player(result, "b").components == ()


def test_runs_per_unit_with_unit_golden() -> None:
    # unit=2: 1 point per 2 runs. 127 // 2 = 63 -> 63.00.
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(point_rule("runs", "per_unit", "1.00", "batting", unit=2),),
    )
    result = _result(ruleset, _snapshot(_bat("a", runs=127)))
    assert _player(result, "a").total == Decimal("63.00")


def test_fours_and_sixes_golden() -> None:
    # 4 fours @ 1.00 + 2 sixes @ 2.00 = 4.00 + 4.00 = 8.00.
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(
            point_rule("fours", "per_unit", "1.00", "batting"),
            point_rule("sixes", "per_unit", "2.00", "batting"),
        ),
    )
    result = _result(ruleset, _snapshot(_bat("a", runs=40, fours=4, sixes=2)))
    p = _player(result, "a")
    assert p.total == Decimal("8.00")
    assert {c.metric for c in p.components} == {"fours", "sixes"}


def test_maiden_per_unit_golden() -> None:
    # 3 maidens @ 5.00 = 15.00.
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(point_rule("maiden", "per_unit", "5.00", "bowling"),),
    )
    result = _result(ruleset, _snapshot(_bowl("a", maidens=3, balls=18, runs_conceded=6)))
    assert _player(result, "a").total == Decimal("15.00")


def test_dot_ball_per_unit_golden() -> None:
    # 12 dot balls @ 0.50 = 6.00.
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(point_rule("dot_ball", "per_unit", "0.50", "bowling"),),
    )
    result = _result(ruleset, _snapshot(_bowl("a", dots=12, balls=18)))
    assert _player(result, "a").total == Decimal("6.00")


def test_catch_and_stumping_golden() -> None:
    # 2 catches @ 8.00 = 16.00 ; 1 stumping @ 10.00 = 10.00 ; total 26.00.
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(
            point_rule("catch", "per_unit", "8.00", "fielding"),
            point_rule("stumping", "per_unit", "10.00", "fielding"),
        ),
    )
    result = _result(ruleset, _snapshot(_field("a", catches=2, stumpings=1)))
    assert _player(result, "a").total == Decimal("26.00")


def test_run_out_flat_golden() -> None:
    # 1 run-out @ 6.00 = 6.00 (flat rate when credit types are not distinguished).
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(point_rule("run_out", "per_unit", "6.00", "fielding"),),
    )
    result = _result(ruleset, _snapshot(_field("a", run_outs=1)))
    assert _player(result, "a").total == Decimal("6.00")


def test_run_out_direct_vs_indirect_golden() -> None:
    # Direct @ 12.00, indirect @ 6.00: 1 each -> 18.00.
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(
            point_rule("run_out_direct", "per_unit", "12.00", "fielding"),
            point_rule("run_out_indirect", "per_unit", "6.00", "fielding"),
        ),
    )
    result = _result(ruleset, _snapshot(_field("a", direct=1, indirect=1)))
    assert _player(result, "a").total == Decimal("18.00")


def test_wicket_per_unit_golden() -> None:
    # 4 wickets @ 25.00 = 100.00.
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(point_rule("wicket", "per_unit", "25.00", "bowling"),),
    )
    result = _result(ruleset, _snapshot(_bowl("a", wickets=4, balls=24)))
    assert _player(result, "a").total == Decimal("100.00")


# --------------------------------------------------------------------------- #
# threshold_bonus (milestones)
# --------------------------------------------------------------------------- #


def test_run_milestones_golden() -> None:
    # 50+ -> +20 ; 100+ -> +40. 127 runs: 20+40 = 60. 49: 0. Exactly 50: 20.
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(
            point_rule("run_milestone", "threshold_bonus", "20.00", "batting", min_value=50),
            point_rule("run_milestone", "threshold_bonus", "40.00", "batting", min_value=100),
        ),
    )
    result = _result(
        ruleset, _snapshot(_bat("a", runs=127), _bat("b", runs=49), _bat("c", runs=50))
    )
    assert _player(result, "a").total == Decimal("60.00")
    assert _player(result, "b").total == Decimal("0.00")
    assert _player(result, "c").total == Decimal("20.00")


def test_multi_catch_bonus_golden() -> None:
    # catches >= 3 -> +10. 3 catches -> 10.00; 2 catches -> 0.
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(
            point_rule("catch", "per_unit", "8.00", "fielding"),
            point_rule("multi_catch_bonus", "threshold_bonus", "10.00", "fielding", min_value=3),
        ),
    )
    result = _result(ruleset, _snapshot(_field("a", catches=3), _field("b", catches=2)))
    assert _player(result, "a").total == Decimal("34.00")  # 24 + 10
    assert _player(result, "b").total == Decimal("16.00")  # 2*8


# --------------------------------------------------------------------------- #
# tiered (count)
# --------------------------------------------------------------------------- #


def test_wicket_haul_highest_only_golden() -> None:
    # 3+ wickets -> +20 ; 5+ -> +30 (highest_only). 4 wickets -> 20; 5 -> 30.
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(
            point_rule(
                "wicket_haul",
                "tiered",
                "0.00",
                "bowling",
                tiers=[(3, "20.00"), (5, "30.00")],
                tier_mode="highest_only",
            ),
        ),
    )
    result = _result(
        ruleset, _snapshot(_bowl("a", wickets=4), _bowl("b", wickets=5), _bowl("c", wickets=2))
    )
    assert _player(result, "a").total == Decimal("20.00")
    assert _player(result, "b").total == Decimal("30.00")
    assert _player(result, "c").total == Decimal("0.00")


def test_wicket_haul_cumulative_golden() -> None:
    # 3+ -> +20 ; 5+ -> +30 (cumulative). 4 wickets -> 20; 6 -> 50; 3 -> 20.
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(
            point_rule(
                "wicket_haul",
                "tiered",
                "0.00",
                "bowling",
                tiers=[(3, "20.00"), (5, "30.00")],
                tier_mode="cumulative",
            ),
        ),
    )
    result = _result(
        ruleset, _snapshot(_bowl("a", wickets=4), _bowl("b", wickets=6), _bowl("c", wickets=3))
    )
    assert _player(result, "a").total == Decimal("20.00")
    assert _player(result, "b").total == Decimal("50.00")
    assert _player(result, "c").total == Decimal("20.00")


# --------------------------------------------------------------------------- #
# tiered (rates) + qualifiers
# --------------------------------------------------------------------------- #


def test_strike_rate_band_highest_only_golden() -> None:
    # SR = runs*100/balls. Bands: SR>=100 -> +10, >=150 -> +20 (highest_only).
    # 60 runs/30 balls = SR 200 -> +20. 36/30 = SR 120 exactly -> +10.
    # 19 balls (< min_balls=20) -> rule not applicable (no component).
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(
            point_rule(
                "strike_rate_band",
                "tiered",
                "0.00",
                "batting",
                tiers=[(100, "10.00"), (150, "20.00")],
                tier_mode="highest_only",
                min_balls=20,
            ),
        ),
    )
    result = _result(
        ruleset,
        _snapshot(
            _bat("a", runs=60, balls=30),  # SR 200
            _bat("b", runs=36, balls=30),  # SR 120 exactly
            _bat("c", runs=30, balls=19),  # 19 balls < min_balls
        ),
    )
    assert _player(result, "a").total == Decimal("20.00")
    assert _player(result, "b").total == Decimal("10.00")
    assert _player(result, "c").total == Decimal("0.00")
    assert _player(result, "c").components == ()


def test_strike_rate_band_cumulative_golden() -> None:
    # Bands SR>=100 +10, >=150 +20 (cumulative). SR 200 -> 30.
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(
            point_rule(
                "strike_rate_band",
                "tiered",
                "0.00",
                "batting",
                tiers=[(100, "10.00"), (150, "20.00")],
                tier_mode="cumulative",
                min_balls=10,
            ),
        ),
    )
    result = _result(ruleset, _snapshot(_bat("a", runs=60, balls=30)))
    assert _player(result, "a").total == Decimal("30.00")


def test_economy_band_golden() -> None:
    # Economy = runs*6/balls. Bands: econ<=6 -> +10, <=4 -> +20 (highest_only).
    # 20 runs/24 balls = econ 5.0 -> +10. 16/24 = 4.0 exactly -> +20.
    # 18 balls (3 overs) < min_overs=4 -> not applicable.
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(
            point_rule(
                "economy_band",
                "tiered",
                "0.00",
                "bowling",
                tiers=[(6, "10.00"), (4, "20.00")],
                tier_mode="highest_only",
                min_overs=4,
            ),
        ),
    )
    result = _result(
        ruleset,
        _snapshot(
            _bowl("a", balls=24, runs_conceded=20),  # econ 5.0
            _bowl("b", balls=24, runs_conceded=16),  # econ 4.0 exactly
            _bowl("c", balls=18, runs_conceded=9),  # 3 overs < min_overs
        ),
    )
    assert _player(result, "a").total == Decimal("10.00")
    assert _player(result, "b").total == Decimal("20.00")
    assert _player(result, "c").total == Decimal("0.00")
    assert _player(result, "c").components == ()


# --------------------------------------------------------------------------- #
# flag / capped
# --------------------------------------------------------------------------- #


def test_playing_xi_flag_golden() -> None:
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(point_rule("playing_xi", "flag", "4.00", "participation"),),
    )
    result = _result(
        ruleset,
        _snapshot(
            PlayerLine(player_id="a", is_playing_xi=True),
            PlayerLine(player_id="b", is_playing_xi=False),
        ),
    )
    assert _player(result, "a").total == Decimal("4.00")
    assert _player(result, "b").total == Decimal("0.00")


def test_duck_penalty_golden() -> None:
    # A duck (0 runs, dismissed) costs -5.00. Retired-hurt with 0 runs is NOT
    # a duck by default.
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(point_rule("duck", "flag", "-5.00", "batting"),),
    )
    result = _result(
        ruleset,
        _snapshot(
            _bat("a", runs=0, dismissal="bowled"),  # duck -> -5
            _bat("b", runs=0, dismissal=None),  # not out 0 -> not a duck
            _bat("c", runs=0, dismissal=None, retired_hurt=True),  # retired hurt -> not a duck
        ),
    )
    assert _player(result, "a").total == Decimal("-5.00")
    assert _player(result, "b").total == Decimal("0.00")
    assert _player(result, "c").total == Decimal("0.00")


def test_bowled_lbw_bonus_golden() -> None:
    # 2 bowled/lbw wickets @ 10.00 = 20.00.
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(point_rule("bowled_lbw_bonus", "per_unit", "10.00", "bowling"),),
    )
    result = _result(ruleset, _snapshot(_bowl("a", bowled_lbw=2, wickets=4)))
    assert _player(result, "a").total == Decimal("20.00")


def test_player_of_match_flag_golden() -> None:
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(point_rule("player_of_match", "flag", "25.00", "participation"),),
    )
    result = _result(
        ruleset,
        _snapshot(PlayerLine(player_id="a", player_of_match=True), PlayerLine(player_id="b")),
    )
    assert _player(result, "a").total == Decimal("25.00")
    assert _player(result, "b").total == Decimal("0.00")


def test_hat_trick_flag_golden() -> None:
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(point_rule("hat_trick", "flag", "50.00", "bowling"),),
    )
    result = _result(ruleset, _snapshot(_bowl("a", hat_trick=True), _bowl("b")))
    assert _player(result, "a").total == Decimal("50.00")
    assert _player(result, "b").total == Decimal("0.00")


def test_capped_golden() -> None:
    # 1 point per run, capped at 100. 127 runs -> 100; 60 runs -> 60.
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(point_rule("runs", "capped", "1.00", "batting", cap_value="100.00"),),
    )
    result = _result(ruleset, _snapshot(_bat("a", runs=127), _bat("b", runs=60)))
    assert _player(result, "a").total == Decimal("100.00")
    assert _player(result, "b").total == Decimal("60.00")


# --------------------------------------------------------------------------- #
# rounding
# --------------------------------------------------------------------------- #


def test_half_up_rounding_golden() -> None:
    # 0.005 rounds half up to 0.01 (not banker's rounding).
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(point_rule("runs", "per_unit", "0.005", "batting"),),
    )
    result = _result(ruleset, _snapshot(_bat("a", runs=1)))
    assert _player(result, "a").total == Decimal("0.01")


def test_quantization_of_rates_golden() -> None:
    # SR 127*100/38 = 334.2105... -> detail value quantized to 334.21.
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(
            point_rule(
                "strike_rate_band",
                "tiered",
                "0.00",
                "batting",
                tiers=[(300, "5.00")],
                tier_mode="highest_only",
                min_balls=10,
            ),
        ),
    )
    result = _result(ruleset, _snapshot(_bat("a", runs=127, balls=38)))
    p = _player(result, "a")
    assert p.total == Decimal("5.00")
    assert p.components[0].detail["metric_value"] == Decimal("334.21")


def test_abandoned_no_points_default() -> None:
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(point_rule("runs", "per_unit", "1.00", "batting"),),
    )
    result = _result(ruleset, _snapshot(_bat("a", runs=50), result_type="ABANDONED"))
    assert result.players == ()
    assert result.is_partial is False


def test_abandoned_compute_policy() -> None:
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(point_rule("runs", "per_unit", "1.00", "batting"),),
        policies=RulesetPolicies(abandoned="compute"),
    )
    result = _result(ruleset, _snapshot(_bat("a", runs=50), result_type="ABANDONED"))
    assert _player(result, "a").total == Decimal("50.00")
