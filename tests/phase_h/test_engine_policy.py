"""Per-ruleset policy flags (Task 2): super over, abandoned, reduced overs,
retirement hurt, substitutes — each with its default and its override."""

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

COVERAGE = SnapshotCoverage(has_ball_data=True, has_dismissal_kinds=True)


def _snapshot(
    *lines: PlayerLine, result_type: str = "NORMAL", super_over: bool = False, reduced: bool = False
):
    return FantasyMatchSnapshot(
        match=MatchContext(match_id="m1", format="T20", result_type=result_type),
        coverage=COVERAGE,
        player_lines=tuple(lines),
        super_over_played=super_over,
        reduced_overs=reduced,
    )


def _runs_ruleset(**policies) -> RulesetSpec:
    return RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(point_rule("runs", "per_unit", "1.00", "batting"),),
        policies=RulesetPolicies(**policies),
    )


def _by_id(result, player_id: str):
    for p in result.players:
        if p.player_id == player_id:
            return p
    return None


def test_super_over_excluded_by_default() -> None:
    # Super Over lines are dropped by default; the main-match line counts.
    ruleset = _runs_ruleset()
    result = compute(
        ruleset,
        _snapshot(
            PlayerLine(player_id="a", runs=50),
            PlayerLine(player_id="b", runs=10, is_super_over=True),
            super_over=True,
        ),
    )
    assert _by_id(result, "a").total == Decimal("50.00")
    assert _by_id(result, "b") is None


def test_super_over_included_policy() -> None:
    ruleset = _runs_ruleset(super_over="include")
    result = compute(
        ruleset,
        _snapshot(PlayerLine(player_id="b", runs=10, is_super_over=True), super_over=True),
    )
    assert _by_id(result, "b").total == Decimal("10.00")


def test_abandoned_no_points_default() -> None:
    result = compute(
        _runs_ruleset(), _snapshot(PlayerLine(player_id="a", runs=50), result_type="NO_RESULT")
    )
    assert result.players == ()


def test_abandoned_compute_override() -> None:
    result = compute(
        _runs_ruleset(abandoned="compute"),
        _snapshot(PlayerLine(player_id="a", runs=50), result_type="ABANDONED"),
    )
    assert _by_id(result, "a").total == Decimal("50.00")


def test_reduced_overs_compute_default() -> None:
    # A reduced-overs match is computed on its actual data by default.
    result = compute(_runs_ruleset(), _snapshot(PlayerLine(player_id="a", runs=50), reduced=True))
    assert _by_id(result, "a").total == Decimal("50.00")


def test_reduced_overs_exclude_policy() -> None:
    result = compute(
        _runs_ruleset(reduced_overs="exclude"),
        _snapshot(PlayerLine(player_id="a", runs=50), reduced=True),
    )
    assert result.players == ()


def test_substitutes_excluded_by_default() -> None:
    result = compute(
        _runs_ruleset(),
        _snapshot(
            PlayerLine(player_id="a", runs=50),
            PlayerLine(player_id="s", runs=20, is_substitute=True),
        ),
    )
    assert _by_id(result, "a") is not None
    assert _by_id(result, "s") is None


def test_substitutes_included_policy() -> None:
    result = compute(
        _runs_ruleset(substitutes="include"),
        _snapshot(PlayerLine(player_id="s", runs=20, is_substitute=True)),
    )
    assert _by_id(result, "s").total == Decimal("20.00")


def test_retirement_hurt_not_duck_by_default() -> None:
    # A retirement-hurt batter on 0 is not a duck; the policy can override.
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(point_rule("duck", "flag", "-5.00", "batting"),),
    )
    result = compute(
        ruleset,
        _snapshot(PlayerLine(player_id="a", runs=0, did_bat=True, retired_hurt=True)),
    )
    assert _by_id(result, "a") is None or _by_id(result, "a").total == Decimal("0.00")


def test_retirement_hurt_count_as_out_policy() -> None:
    ruleset = RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(point_rule("duck", "flag", "-5.00", "batting"),),
        policies=RulesetPolicies(retirement_hurt="count_as_out"),
    )
    result = compute(
        ruleset,
        _snapshot(PlayerLine(player_id="a", runs=0, did_bat=True, retired_hurt=True)),
    )
    assert _by_id(result, "a").total == Decimal("-5.00")
