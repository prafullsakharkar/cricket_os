"""Oracle test (Task 2): an independent, deliberately naive reference
calculator re-derives the engine's outputs from raw fixture rows.

The oracle shares nothing with the engine's implementation (no
``metrics_registry``, no ``_metric_value``, no policies machinery) — it is
plain Decimal arithmetic written straight from the same *documented* rule
semantics. Where the engine and the oracle agree across a broad fixture set,
both are very unlikely to share the same bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

import pytest

from apps.fantasy.engine import (
    FantasyMatchSnapshot,
    MatchContext,
    PlayerLine,
    RulesetSpec,
    SnapshotCoverage,
    compute,
    point_rule,
)

FULL = SnapshotCoverage(
    has_ball_data=True,
    has_dismissal_kinds=True,
    has_fielding_data=True,
    has_fielder_credit_types=True,
)

Q = Decimal("0.01")


def _q(value: Decimal) -> Decimal:
    return value.quantize(Q, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- #
# The oracle: one dict per player, raw fields, naive arithmetic.
# --------------------------------------------------------------------------- #


@dataclass
class OracleRow:
    pid: str
    runs: int = 0
    balls: int = 0
    fours: int = 0
    sixes: int = 0
    out: bool = False
    wickets: int = 0
    balls_bowled: int = 0
    runs_conceded: int = 0
    catches: int = 0
    maidens: int = 0
    dots: int = 0


def _sr(row: OracleRow) -> Decimal:
    if row.balls <= 0:
        return Decimal("-1")  # not applicable sentinel
    return Decimal(row.runs * 100) / Decimal(row.balls)


def _econ(row: OracleRow) -> Decimal:
    if row.balls_bowled <= 0:
        return Decimal("-1")
    return Decimal(row.runs_conceded * 6) / Decimal(row.balls_bowled)


def _oracle_points(row: OracleRow, rule) -> Decimal | None:
    """Naive re-derivation of one rule's points (None = not applicable)."""
    metric = rule.metric
    operator = rule.operator
    if metric == "runs":
        value = row.runs
    elif metric == "fours":
        value = row.fours
    elif metric == "sixes":
        value = row.sixes
    elif metric == "wicket":
        value = row.wickets
    elif metric == "catch":
        value = row.catches
    elif metric == "maiden":
        value = row.maidens
    elif metric == "dot_ball":
        value = row.dots
    elif metric == "duck":
        value = 1 if (row.out and row.runs == 0) else 0
    elif metric == "run_milestone":
        value = row.runs
    elif metric == "strike_rate_band":
        if row.balls < rule.min_balls:
            return None
        return _tier_points(_sr(row), rule)
    elif metric == "economy_band":
        overs = Decimal(row.balls_bowled) / Decimal(6)
        if overs < Decimal(rule.min_overs):
            return None
        return _tier_points(_econ(row), rule)
    else:  # pragma: no cover - oracle covers the golden rule set
        raise AssertionError(f"oracle missing metric {metric}")

    if operator == "per_unit":
        unit = rule.unit or 1
        return _q(rule.points * (value // unit))
    if operator == "flag":
        return _q(rule.points) if value else Decimal("0.00")
    if operator == "threshold_bonus":
        return _q(rule.points) if value >= rule.min_value else Decimal("0.00")
    if operator == "tiered":
        return _tier_points(Decimal(value), rule)
    if operator == "capped":
        unit = rule.unit or 1
        return _q(min(rule.points * (value // unit), Decimal(rule.cap_value)))
    raise AssertionError(operator)  # pragma: no cover


def _tier_points(rate: Decimal, rule) -> Decimal | None:
    """Naive tier walk: find reached tiers by the metric's comparison direction."""
    if rule.metric == "economy_band":
        reached = [t for t in rule.tiers if rate <= Decimal(t.min_value)]
    else:
        reached = [t for t in rule.tiers if rate >= Decimal(t.min_value)]
    if not reached:
        return Decimal("0.00")
    if rule.tier_mode == "highest_only":
        return _q(reached[-1].points)
    return _q(sum(t.points for t in reached))


def _oracle_total(row: OracleRow, ruleset: RulesetSpec) -> Decimal:
    total = Decimal("0.00")
    for rule in ruleset.rules:
        pts = _oracle_points(row, rule)
        if pts is not None:
            total += pts
    return _q(total)


# --------------------------------------------------------------------------- #
# Fixtures: rows built from raw numbers, engine lines built independently.
# --------------------------------------------------------------------------- #

ROWS = [
    # (pid, runs, balls, fours, sixes, out, wkt, bb, rc, ct, ma, dots)
    OracleRow(
        "p1",
        runs=127,
        balls=38,
        fours=14,
        sixes=4,
        out=True,
        wickets=1,
        balls_bowled=12,
        runs_conceded=18,
        catches=2,
        dots=3,
    ),
    OracleRow("p2", runs=0, balls=1, out=True),
    OracleRow(
        "p3",
        runs=52,
        balls=47,
        fours=3,
        sixes=1,
        out=False,
        wickets=4,
        balls_bowled=24,
        runs_conceded=20,
        catches=0,
        maidens=1,
        dots=9,
    ),
    OracleRow(
        "p4", runs=30, balls=20, out=False, wickets=3, balls_bowled=18, runs_conceded=14, catches=1
    ),
    OracleRow(
        "p5",
        runs=8,
        balls=6,
        fours=1,
        out=True,
        wickets=0,
        balls_bowled=6,
        runs_conceded=12,
        catches=0,
    ),
]


def _engine_line(row: OracleRow) -> PlayerLine:
    return PlayerLine(
        player_id=row.pid,
        is_playing_xi=True,
        did_bat=True,
        did_bowl=True,
        runs=row.runs,
        balls_faced=row.balls,
        fours=row.fours,
        sixes=row.sixes,
        dismissal_kind="caught" if row.out else None,
        wickets=row.wickets,
        balls_bowled=row.balls_bowled,
        runs_conceded=row.runs_conceded,
        catches=row.catches,
        maidens=row.maidens,
        dots_bowled=row.dots,
    )


def _oracle_ruleset() -> RulesetSpec:
    return RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(
            # ``capped`` is the runs accrual here: 1 point per run, max 100.
            point_rule("runs", "capped", "1.00", "batting", cap_value="100.00"),
            point_rule("fours", "per_unit", "1.00", "batting"),
            point_rule("sixes", "per_unit", "2.00", "batting"),
            point_rule("wicket", "per_unit", "25.00", "bowling"),
            point_rule("catch", "per_unit", "8.00", "fielding"),
            point_rule("maiden", "per_unit", "5.00", "bowling"),
            point_rule("dot_ball", "per_unit", "0.50", "bowling"),
            point_rule("duck", "flag", "-5.00", "batting"),
            point_rule("run_milestone", "threshold_bonus", "20.00", "batting", min_value=50),
            point_rule(
                "strike_rate_band",
                "tiered",
                "0.00",
                "batting",
                tiers=[(100, "10.00"), (150, "20.00")],
                tier_mode="highest_only",
                min_balls=20,
            ),
            point_rule(
                "economy_band",
                "tiered",
                "0.00",
                "bowling",
                tiers=[(8, "10.00"), (6, "20.00")],
                tier_mode="highest_only",
                min_overs=2,
            ),
        ),
    )


def test_engine_matches_oracle_across_fixture_rows() -> None:
    ruleset = _oracle_ruleset()
    snapshot = FantasyMatchSnapshot(
        match=MatchContext(match_id="m1", result_type="NORMAL"),
        coverage=FULL,
        player_lines=tuple(_engine_line(r) for r in ROWS),
    )
    result = compute(ruleset, snapshot)

    assert len(result.players) == len(ROWS)
    engine_by_id = {p.player_id: p for p in result.players}
    for row in ROWS:
        engine_total = engine_by_id[row.pid].total
        oracle_total = _oracle_total(row, ruleset)
        assert engine_total == oracle_total, (
            f"player {row.pid}: engine {engine_total} != oracle {oracle_total}"
        )
        # And total == sum of engine components.
        components = engine_by_id[row.pid].components
        assert engine_total == _q(sum(c.points for c in components))


def test_engine_component_points_match_oracle() -> None:
    ruleset = _oracle_ruleset()
    snapshot = FantasyMatchSnapshot(
        match=MatchContext(match_id="m1", result_type="NORMAL"),
        coverage=FULL,
        player_lines=tuple(_engine_line(r) for r in ROWS),
    )
    result = compute(ruleset, snapshot)
    engine_by_id = {p.player_id: p for p in result.players}
    for row in ROWS:
        # For each metric present in the engine breakdown, the oracle must
        # award the identical points.
        oracle = {r.metric: _oracle_points(row, r) for r in ruleset.rules}
        for component in engine_by_id[row.pid].components:
            expected = oracle.get(component.metric)
            assert expected is not None, f"{row.pid} unexpected component {component.metric}"
            assert component.points == expected, (
                f"{row.pid} {component.metric}: engine {component.points} != oracle {expected}"
            )
            # Non-zero components only.
            assert component.points != 0


@pytest.mark.parametrize(
    "pid,expected_total",
    [
        # Hand-checked totals (independent of both implementations).
        # p1: 127 runs capped @100 -> 100 + 14 fours (14) + 4 sixes (8)
        #     + 1 wicket (25) + 2 catches (16) + 3 dots (1.50) + milestone (20)
        #     + SR 334.21 >=150 (20) = 204.50 ; duck no (127) ; econ 9.0 no
        ("p1", "204.50"),
        # p2: 0 runs duck -> -5.00 ; milestone no ; SR not applicable (1 ball) ; total -5.00
        ("p2", "-5.00"),
        # p3: 52 runs (52) + 3 fours (3) + 1 six (2) + 4 wickets (100) + 1 maiden (5)
        #     + 9 dots (4.50) + milestone 50+ (20) + SR 52*100/47=110.6 >=100 (10)
        #     + econ 20*6/24=5.0 <=6 (20) = 216.50
        ("p3", "216.50"),
        # p4: 30 runs + 3 wickets (75) + 1 catch (8) + SR 150 exactly (20) + econ 14*6/18=4.67 (20)
        #     = 30+75+8+20+20 = 153.00
        ("p4", "153.00"),
        # p5: 8 runs + 1 four (1) ; SR not applicable (6 balls < min_balls 20) ;
        #     econ 12*6/6=12.0 >8 no -> 8+1 = 9.00
        ("p5", "9.00"),
    ],
)
def test_hand_checked_totals(pid: str, expected_total: str) -> None:
    """Totals re-derived by hand (a third, independent check)."""
    ruleset = _oracle_ruleset()
    snapshot = FantasyMatchSnapshot(
        match=MatchContext(match_id="m1", result_type="NORMAL"),
        coverage=FULL,
        player_lines=tuple(_engine_line(r) for r in ROWS),
    )
    result = compute(ruleset, snapshot)
    engine_by_id = {p.player_id: p for p in result.players}
    assert engine_by_id[pid].total == Decimal(expected_total)
