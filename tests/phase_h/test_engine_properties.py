"""Engine properties (Task 2): determinism, order independence, the
total==sum(components) invariant, monotonicity, and content-hash stability.

Determinism is checked both in-process (repeat calls) and across processes
(different ``PYTHONHASHSEED`` values must not change the computed digests) —
the strongest form of "same inputs, byte-identical points".
"""

from __future__ import annotations

import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from apps.fantasy.engine import (
    FantasyMatchSnapshot,
    MatchContext,
    PlayerLine,
    RulesetPolicies,
    RulesetSpec,
    SnapshotCoverage,
    compute,
    content_hash,
    point_rule,
)

FULL = SnapshotCoverage(
    has_ball_data=True,
    has_dismissal_kinds=True,
    has_fielding_data=True,
    has_fielder_credit_types=True,
)


def _base_ruleset() -> RulesetSpec:
    return RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=(
            point_rule("runs", "per_unit", "1.00", "batting"),
            point_rule("fours", "per_unit", "1.00", "batting"),
            point_rule("sixes", "per_unit", "2.00", "batting"),
            point_rule("wicket", "per_unit", "25.00", "bowling"),
            point_rule("catch", "per_unit", "8.00", "fielding"),
            point_rule("duck", "flag", "-5.00", "batting"),
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
                "run_milestone",
                "threshold_bonus",
                "20.00",
                "batting",
                min_value=50,
            ),
        ),
        policies=RulesetPolicies(),
    )


def _base_lines() -> tuple[PlayerLine, ...]:
    return (
        PlayerLine(
            player_id="p1",
            is_playing_xi=True,
            did_bat=True,
            did_bowl=True,
            did_field=True,
            runs=58,
            balls_faced=42,
            fours=5,
            sixes=2,
            dismissal_kind="caught",
            wickets=2,
            balls_bowled=18,
            runs_conceded=24,
            catches=1,
        ),
        PlayerLine(
            player_id="p2",
            is_playing_xi=True,
            did_bat=True,
            runs=0,
            balls_faced=1,
            dismissal_kind="bowled",
        ),
        PlayerLine(
            player_id="p3",
            is_playing_xi=True,
            did_bowl=True,
            wickets=4,
            balls_bowled=24,
            runs_conceded=20,
            dots_bowled=9,
        ),
    )


def _snapshot(*lines: PlayerLine, coverage: SnapshotCoverage = FULL) -> FantasyMatchSnapshot:
    return FantasyMatchSnapshot(
        match=MatchContext(match_id="m1", format="T20", result_type="NORMAL"),
        coverage=coverage,
        player_lines=tuple(lines),
    )


def test_repeat_calls_are_identical() -> None:
    ruleset = _base_ruleset()
    snapshot = _snapshot(*_base_lines())
    a = compute(ruleset, snapshot)
    b = compute(ruleset, snapshot)
    assert a.digest == b.digest
    assert [(p.player_id, p.total) for p in a.players] == [
        (p.player_id, p.total) for p in b.players
    ]


def test_total_equals_sum_of_components() -> None:
    ruleset = _base_ruleset()
    result = compute(ruleset, _snapshot(*_base_lines()))
    for player in result.players:
        expected = sum((c.points for c in player.components), Decimal("0.00"))
        assert player.total == expected
        # Every component is quantized to 2dp.
        for c in player.components:
            assert c.points == c.points.quantize(Decimal("0.01"))


def test_rule_order_does_not_change_result() -> None:
    ruleset = _base_ruleset()
    reversed_rules = RulesetSpec(
        ruleset_id=ruleset.ruleset_id,
        version=ruleset.version,
        rules=tuple(reversed(ruleset.rules)),
        policies=ruleset.policies,
    )
    snapshot = _snapshot(*_base_lines())
    assert compute(reversed_rules, snapshot).digest == compute(ruleset, snapshot).digest


def test_player_line_order_does_not_change_result() -> None:
    ruleset = _base_ruleset()
    lines = _base_lines()
    forward = compute(ruleset, _snapshot(*lines))
    backward = compute(ruleset, _snapshot(*reversed(lines)))
    assert forward.digest == backward.digest
    assert [p.player_id for p in forward.players] == [p.player_id for p in backward.players]


def test_content_hash_stable_under_rule_order() -> None:
    ruleset = _base_ruleset()
    shuffled = RulesetSpec(
        ruleset_id=ruleset.ruleset_id,
        version=ruleset.version,
        rules=tuple(reversed(ruleset.rules)),
        policies=ruleset.policies,
    )
    assert content_hash(ruleset) == content_hash(shuffled)


def test_content_hash_changes_when_a_value_changes() -> None:
    ruleset = _base_ruleset()
    changed = RulesetSpec(
        ruleset_id=ruleset.ruleset_id,
        version=ruleset.version,
        rules=tuple(
            point_rule("sixes", "per_unit", "3.00", "batting") if r.metric == "sixes" else r
            for r in ruleset.rules
        ),
        policies=ruleset.policies,
    )
    assert content_hash(ruleset) != content_hash(changed)


def test_monotonicity_runs_never_decrease_points() -> None:
    """Raising a player's runs must never lower their total (the runs rule and
    the run-milestone bonus both reward more runs; the duck penalty is the only
    counterexample and it only fires at exactly 0 runs)."""
    ruleset = _base_ruleset()
    lows = _snapshot(
        PlayerLine(player_id="a", is_playing_xi=True, did_bat=True, runs=40, balls_faced=30)
    )
    highs = _snapshot(
        PlayerLine(player_id="a", is_playing_xi=True, did_bat=True, runs=90, balls_faced=60)
    )
    low = next(p.total for p in compute(ruleset, lows).players if p.player_id == "a")
    high = next(p.total for p in compute(ruleset, highs).players if p.player_id == "a")
    assert high >= low


# --------------------------------------------------------------------------- #
# Cross-process determinism across hash seeds
# --------------------------------------------------------------------------- #


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / ".git").exists():
            return parent
    raise RuntimeError("could not locate repository root")


ROOT = _repo_root()

_CODE = """
import sys
sys.path.insert(0, {root!r})
from apps.fantasy.engine import (FantasyMatchSnapshot, MatchContext, PlayerLine,
    RulesetSpec, SnapshotCoverage, compute, content_hash, point_rule)
full = SnapshotCoverage(True, True, True)
ruleset = RulesetSpec(
    ruleset_id="r", version=1,
    rules=(
        point_rule("runs", "per_unit", "1.00", "batting"),
        point_rule("wicket", "per_unit", "25.00", "bowling"),
        point_rule("duck", "flag", "-5.00", "batting"),
        point_rule("strike_rate_band", "tiered", "0.00", "batting",
                   tiers=[(100, "10.00")], tier_mode="highest_only", min_balls=20),
    ),
)
snapshot = FantasyMatchSnapshot(
    match=MatchContext(match_id="m1", format="T20", result_type="NORMAL"),
    coverage=full,
    player_lines=(
        PlayerLine(player_id="p1", is_playing_xi=True, did_bat=True, runs=58,
                   balls_faced=42, dismissal_kind="caught"),
        PlayerLine(player_id="p2", is_playing_xi=True, did_bat=True, runs=0,
                   balls_faced=1, dismissal_kind="bowled"),
    ),
)
r = compute(ruleset, snapshot)
print(content_hash(ruleset), r.digest, [(p.player_id, str(p.total)) for p in r.players])
"""


def test_determinism_across_hash_seeds() -> None:
    outputs = set()
    for seed in ("0", "1", "424242"):
        env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONHASHSEED": seed}
        completed = subprocess.run(
            [sys.executable, "-c", _CODE.format(root=repr(str(ROOT)))],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert completed.returncode == 0, completed.stderr
        outputs.add(completed.stdout.strip())
    assert len(outputs) == 1, f"results differ across hash seeds: {outputs}"


# --------------------------------------------------------------------------- #
# Hypothesis: determinism under generated line data
# --------------------------------------------------------------------------- #


@st.composite
def _line_strategy(draw):
    return PlayerLine(
        player_id=draw(st.text(min_size=1, max_size=8)),
        is_playing_xi=draw(st.booleans()),
        did_bat=draw(st.booleans()),
        did_bowl=draw(st.booleans()),
        runs=draw(st.integers(min_value=0, max_value=400)),
        balls_faced=draw(st.integers(min_value=0, max_value=200)),
        fours=draw(st.integers(min_value=0, max_value=40)),
        sixes=draw(st.integers(min_value=0, max_value=30)),
        dismissal_kind=draw(st.sampled_from(["bowled", "caught", "not_out", "retired_hurt"])),
        wickets=draw(st.integers(min_value=0, max_value=10)),
        balls_bowled=draw(st.integers(min_value=0, max_value=60)),
        runs_conceded=draw(st.integers(min_value=0, max_value=200)),
        catches=draw(st.integers(min_value=0, max_value=5)),
    )


@settings(max_examples=50, deadline=None)
@given(st.lists(_line_strategy(), min_size=0, max_size=6))
def test_compute_is_deterministic_under_generated_lines(lines: list[PlayerLine]) -> None:
    ruleset = _base_ruleset()
    snapshot = _snapshot(*lines)
    a = compute(ruleset, snapshot)
    b = compute(ruleset, snapshot)
    assert a.digest == b.digest
    assert a.unsupported_rules == b.unsupported_rules
    assert a.is_partial == b.is_partial
