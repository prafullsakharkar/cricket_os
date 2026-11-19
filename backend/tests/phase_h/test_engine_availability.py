"""Data-requirements registry (Task 2, tension 2): a rule whose inputs the
snapshot lacks is skipped, listed in ``unsupported_rules``, and the result is
flagged partial — never guessed."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.fantasy.engine import (
    FantasyMatchSnapshot,
    MatchContext,
    PlayerLine,
    RulesetSpec,
    SnapshotCoverage,
    compute,
    data_requirements,
    metric_spec,
    point_rule,
)


def _snapshot(coverage: SnapshotCoverage, *lines: PlayerLine) -> FantasyMatchSnapshot:
    return FantasyMatchSnapshot(
        match=MatchContext(match_id="m1", result_type="NORMAL"),
        coverage=coverage,
        player_lines=tuple(lines),
    )


def _ruleset(*rules) -> RulesetSpec:
    return RulesetSpec(ruleset_id="r", version=1, rules=tuple(rules))


def test_ball_dependent_rules_skipped_without_ball_data() -> None:
    summary = SnapshotCoverage(has_ball_data=False)
    ruleset = _ruleset(
        point_rule("runs", "per_unit", "1.00", "batting"),
        point_rule("balls", "per_unit", "0.50", "batting"),
        point_rule(
            "strike_rate_band",
            "tiered",
            "0.00",
            "batting",
            tiers=[(100, "10.00")],
            tier_mode="highest_only",
            min_balls=10,
        ),
    )
    result = compute(
        ruleset,
        _snapshot(summary, PlayerLine(player_id="a", runs=50, balls_faced=40)),
    )
    # runs is available; balls and strike_rate_band are not.
    assert "balls" in result.unsupported_rules
    assert "strike_rate_band" in result.unsupported_rules
    assert result.is_partial is True
    assert _by_id(result, "a").total == Decimal("50.00")


def test_dismissal_metrics_skipped_without_dismissal_kinds() -> None:
    no_kinds = SnapshotCoverage(has_ball_data=True, has_dismissal_kinds=False)
    ruleset = _ruleset(
        point_rule("duck", "flag", "-5.00", "batting"),
        point_rule("bowled_lbw_bonus", "per_unit", "10.00", "bowling"),
    )
    result = compute(
        ruleset,
        _snapshot(
            no_kinds, PlayerLine(player_id="a", runs=0, did_bat=True, dismissal_kind="bowled")
        ),
    )
    assert "duck" in result.unsupported_rules
    assert "bowled_lbw_bonus" in result.unsupported_rules
    assert result.is_partial is True


def test_fielding_metrics_skipped_without_fielding_data() -> None:
    """Flat fielding metrics need the match's fielding counts (never guess 0)."""
    no_fielding = SnapshotCoverage(
        has_ball_data=True, has_dismissal_kinds=True, has_fielding_data=False
    )
    ruleset = _ruleset(
        point_rule("catch", "per_unit", "8.00", "fielding"),
        point_rule("stumping", "per_unit", "10.00", "fielding"),
        point_rule("run_out", "per_unit", "6.00", "fielding"),
    )
    result = compute(
        ruleset,
        _snapshot(
            no_fielding,
            PlayerLine(player_id="a", catches=1, stumpings=1, run_outs_total=1),
        ),
    )
    assert "catch" in result.unsupported_rules
    assert "stumping" in result.unsupported_rules
    assert "run_out" in result.unsupported_rules
    assert result.is_partial is True
    assert _by_id(result, "a").total == Decimal("0.00")


def test_run_out_direct_requires_fielding_data_and_credit_types() -> None:
    """run_out_direct/indirect additionally need direct-vs-relay attribution."""
    no_credit = SnapshotCoverage(
        has_ball_data=True,
        has_dismissal_kinds=True,
        has_fielding_data=True,
        has_fielder_credit_types=False,
    )
    ruleset = _ruleset(point_rule("run_out_direct", "per_unit", "12.00", "fielding"))
    result = compute(
        ruleset,
        _snapshot(no_credit, PlayerLine(player_id="a", run_outs_total=1, run_outs_direct=1)),
    )
    assert "run_out_direct" in result.unsupported_rules


def test_fielding_metrics_computed_with_fielding_data() -> None:
    fielding = SnapshotCoverage(
        has_ball_data=True,
        has_dismissal_kinds=True,
        has_fielding_data=True,
        has_fielder_credit_types=True,
    )
    ruleset = _ruleset(
        point_rule("catch", "per_unit", "8.00", "fielding"),
        point_rule("run_out_direct", "per_unit", "12.00", "fielding"),
    )
    result = compute(
        ruleset,
        _snapshot(
            fielding, PlayerLine(player_id="a", catches=2, run_outs_direct=1, run_outs_total=1)
        ),
    )
    assert result.unsupported_rules == ()
    assert _by_id(result, "a").total == Decimal("28.00")  # 16 + 12


def test_full_coverage_supports_every_metric() -> None:
    full = SnapshotCoverage(
        has_ball_data=True,
        has_dismissal_kinds=True,
        has_fielding_data=True,
        has_fielder_credit_types=True,
    )
    line = PlayerLine(
        player_id="a",
        is_playing_xi=True,
        did_bat=True,
        did_bowl=True,
        did_field=True,
        runs=50,
        balls_faced=40,
        fours=4,
        sixes=2,
        dismissal_kind="caught",
        wickets=2,
        balls_bowled=24,
        runs_conceded=30,
        maidens=1,
        dots_bowled=6,
        bowled_lbw_wickets=1,
        catches=2,
        stumpings=0,
        run_outs_total=1,
        run_outs_direct=1,
        run_outs_indirect=0,
        player_of_match=True,
        hat_trick=False,
    )
    metrics = [
        "playing_xi",
        "runs",
        "balls",
        "fours",
        "sixes",
        "duck",
        "run_milestone",
        "strike_rate_band",
        "wicket",
        "bowled_lbw_bonus",
        "wicket_haul",
        "maiden",
        "economy_band",
        "dot_ball",
        "catch",
        "multi_catch_bonus",
        "stumping",
        "run_out",
        "run_out_direct",
        "run_out_indirect",
        "player_of_match",
        "hat_trick",
    ]
    rules = []
    for m in metrics:
        spec = metric_spec(m)
        assert spec is not None, m
        if spec.kind == "boolean":
            rules.append(point_rule(m, "flag", "1.00", spec.scope))
        elif m in ("strike_rate_band", "economy_band"):
            kw = {"min_balls": 10} if m == "strike_rate_band" else {"min_overs": 2}
            rules.append(
                point_rule(
                    m,
                    "tiered",
                    "0.00",
                    spec.scope,
                    tiers=[(0, "1.00")],
                    tier_mode="highest_only",
                    **kw,
                )
            )
        else:
            rules.append(point_rule(m, "per_unit", "1.00", spec.scope))
    result = compute(_ruleset(*rules), _snapshot(full, line))
    assert result.unsupported_rules == ()
    assert result.is_partial is False


def test_registry_declares_every_metric_and_its_inputs() -> None:
    for key in (
        "playing_xi",
        "runs",
        "balls",
        "fours",
        "sixes",
        "duck",
        "run_milestone",
        "strike_rate_band",
        "wicket",
        "bowled_lbw_bonus",
        "wicket_haul",
        "maiden",
        "economy_band",
        "dot_ball",
        "catch",
        "multi_catch_bonus",
        "stumping",
        "run_out",
        "run_out_direct",
        "run_out_indirect",
        "player_of_match",
        "hat_trick",
    ):
        req = data_requirements(key)
        assert req is not None, key
        assert req.reads, key


@pytest.mark.parametrize(
    "key,needs_ball,needs_kinds,needs_fielding,needs_credit",
    [
        ("balls", True, False, False, False),
        ("strike_rate_band", True, False, False, False),
        ("maiden", True, False, False, False),
        ("economy_band", True, False, False, False),
        ("dot_ball", True, False, False, False),
        ("duck", False, True, False, False),
        ("bowled_lbw_bonus", False, True, False, False),
        ("catch", False, False, True, False),
        ("multi_catch_bonus", False, False, True, False),
        ("stumping", False, False, True, False),
        ("run_out", False, False, True, False),
        ("run_out_direct", False, False, True, True),
        ("run_out_indirect", False, False, True, True),
        ("runs", False, False, False, False),
        ("playing_xi", False, False, False, False),
        ("player_of_match", False, False, False, False),
    ],
)
def test_registry_availability_flags(
    key, needs_ball, needs_kinds, needs_fielding, needs_credit
) -> None:
    spec = data_requirements(key)
    assert spec.needs_ball_data is needs_ball
    assert spec.needs_dismissal_kinds is needs_kinds
    assert spec.needs_fielding_data is needs_fielding
    assert spec.needs_fielder_credit_types is needs_credit


def _by_id(result, player_id: str):
    for p in result.players:
        if p.player_id == player_id:
            return p
    raise AssertionError(f"player {player_id} missing")
