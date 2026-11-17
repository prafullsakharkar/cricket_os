"""The pure compute function (Task 2).

:func:`compute` is deterministic and order-independent: the same (ruleset,
snapshot) always produces byte-identical points. It performs no I/O, reads no
clock, and uses no randomness. All point arithmetic is ``Decimal`` (quantized
to 2dp with ROUND_HALF_UP); rate-band comparisons use exact cross-
multiplication (no floats anywhere).

Pipeline:

1. Validate the ruleset (defensive; authoring also validates).
2. Apply the per-ruleset policies (super over / abandoned / reduced overs /
   retirement-hurt / substitutes) to decide which lines participate and
   whether the match yields any points at all.
3. Split rules into available vs unsupported via the data-requirements
   registry (decision 8): a rule whose inputs the snapshot lacks is skipped
   for every player, listed in ``unsupported_rules``, and the result is
   flagged ``is_partial`` — never guessed.
4. Evaluate each available rule for each participating line, quantizing every
   rule result and the per-player total.
"""

from __future__ import annotations

from decimal import Decimal

from apps.fantasy.engine.digest import result_digest
from apps.fantasy.engine.metrics_registry import metric_spec
from apps.fantasy.engine.rules import validate_ruleset
from apps.fantasy.engine.types import (
    NO_CONTEST_RESULT_TYPES,
    OUT_DISMISSAL_KINDS,
    ComponentPoints,
    FantasyMatchSnapshot,
    FantasyResult,
    PlayerLine,
    PlayerPoints,
    PointRuleSpec,
    RulesetPolicies,
    RulesetSpec,
    SnapshotCoverage,
    q2,
)


def _rule_available(rule: PointRuleSpec, coverage: SnapshotCoverage) -> bool:
    """A rule is computable only when its metric's inputs exist (decision 8)."""
    spec = metric_spec(rule.metric)
    if spec is None:
        return False  # pragma: no cover - validate_ruleset rejects unknown metrics
    missing_inputs = (
        (spec.needs_ball_data and not coverage.has_ball_data)
        or (spec.needs_dismissal_kinds and not coverage.has_dismissal_kinds)
        or (spec.needs_fielding_data and not coverage.has_fielding_data)
        or (spec.needs_fielder_credit_types and not coverage.has_fielder_credit_types)
    )
    return not missing_inputs


def _metric_value(metric: str, line: PlayerLine, policies: RulesetPolicies) -> int | Decimal | None:
    """The display/raw value of a metric for a line (None = not applicable)."""
    if metric == "playing_xi":
        return int(line.is_playing_xi)
    if metric == "runs":
        return line.runs
    if metric == "balls":
        return line.balls_faced if line.balls_faced is not None else 0
    if metric == "fours":
        return line.fours
    if metric == "sixes":
        return line.sixes
    if metric == "duck":
        dismissed = line.dismissal_kind in OUT_DISMISSAL_KINDS
        if not dismissed and line.retired_hurt and policies.retirement_hurt == "count_as_out":
            dismissed = True
        return int(dismissed and line.runs == 0)
    if metric == "run_milestone":
        return line.runs
    if metric == "strike_rate_band":
        if line.balls_faced is None or line.balls_faced <= 0:
            return None  # pragma: no cover - rate metrics only reach here via forbidden operators
        return q2(Decimal(line.runs * 100) / Decimal(line.balls_faced))
    if metric == "wicket":
        return line.wickets
    if metric == "bowled_lbw_bonus":
        return line.bowled_lbw_wickets
    if metric == "wicket_haul":
        return line.wickets
    if metric == "maiden":
        return line.maidens if line.maidens is not None else 0
    if metric == "economy_band":
        if line.balls_bowled is None or line.balls_bowled <= 0 or line.runs_conceded is None:
            return None  # pragma: no cover - rate metrics only reach here via forbidden operators
        return q2(Decimal(line.runs_conceded * 6) / Decimal(line.balls_bowled))
    if metric == "dot_ball":
        return line.dots_bowled if line.dots_bowled is not None else 0
    if metric == "catch":
        return line.catches
    if metric == "multi_catch_bonus":
        return line.catches
    if metric == "stumping":
        return line.stumpings
    if metric == "run_out":
        return line.run_outs_total
    if metric == "run_out_direct":
        return line.run_outs_direct if line.run_outs_direct is not None else 0
    if metric == "run_out_indirect":
        return line.run_outs_indirect if line.run_outs_indirect is not None else 0
    if metric == "player_of_match":
        return int(line.player_of_match)
    if metric == "hat_trick":
        return int(line.hat_trick)
    return None  # pragma: no cover - registry and extractor must stay in sync


def _meets_threshold(
    metric: str, line: PlayerLine, threshold: int | Decimal, policies: RulesetPolicies
) -> bool:
    """Exact threshold test (cross-multiplication for rate metrics — no floats).

    - strike_rate >= T  ⟺  runs*100 >= T*balls_faced
    - economy   <= T  ⟺  runs_conceded*6 <= T*balls_bowled
    - counts:            value >= T
    """
    if metric == "strike_rate_band":
        if line.balls_faced is None:
            return False  # pragma: no cover - snapshots always carry balls_faced
        return Decimal(line.runs * 100) >= Decimal(threshold) * Decimal(line.balls_faced)
    if metric == "economy_band":
        if line.runs_conceded is None or line.balls_bowled is None:
            return False  # pragma: no cover - snapshots always carry both fields
        return Decimal(line.runs_conceded * 6) <= Decimal(threshold) * Decimal(line.balls_bowled)
    value = _metric_value(metric, line, policies)
    return Decimal(value if value is not None else 0) >= Decimal(threshold)


def _rate_applies(metric: str, line: PlayerLine, rule: PointRuleSpec) -> bool:
    """Qualifier gate for rate-band rules (min_balls / min_overs)."""
    if metric == "strike_rate_band":
        if line.balls_faced is None or line.balls_faced <= 0:
            return False
        return rule.min_balls is None or line.balls_faced >= rule.min_balls
    if metric == "economy_band":
        if line.balls_bowled is None or line.balls_bowled <= 0:
            return False
        overs = Decimal(line.balls_bowled) / Decimal(6)
        return rule.min_overs is None or overs >= Decimal(rule.min_overs)
    return True


def _rule_points(
    rule: PointRuleSpec, metric: str, line: PlayerLine, policies: RulesetPolicies
) -> Decimal | None:
    """Points this rule awards for this line, or None when the line can't have it."""
    if rule.operator == "flag":
        return q2(rule.points) if _metric_value(metric, line, policies) else Decimal("0.00")

    if rule.operator == "per_unit":
        value = _metric_value(metric, line, policies)
        if value is None:
            return None  # pragma: no cover - only rate metrics return None (forbidden here)
        return q2(rule.points * (int(value) // (rule.unit or 1)))

    if rule.operator == "capped":
        value = _metric_value(metric, line, policies)
        if value is None:
            return None  # pragma: no cover - only rate metrics return None (forbidden here)
        assert rule.cap_value is not None  # validation: capped requires cap_value
        raw = rule.points * (int(value) // (rule.unit or 1))
        return q2(min(raw, rule.cap_value))

    if rule.operator == "threshold_bonus":
        if not _rate_applies(metric, line, rule):
            return None  # pragma: no cover - threshold_bonus on rate metrics is forbidden
        assert rule.min_value is not None  # validation: threshold_bonus requires min_value
        if _meets_threshold(metric, line, rule.min_value, policies):
            return q2(rule.points)
        return Decimal("0.00")

    if rule.operator == "tiered":
        if not _rate_applies(metric, line, rule):
            return None
        reached = [t for t in rule.tiers if _meets_threshold(metric, line, t.min_value, policies)]
        if not reached:
            return Decimal("0.00")
        if rule.tier_mode == "highest_only":
            return q2(reached[-1].points)
        return q2(sum(t.points for t in reached))

    return None  # pragma: no cover - validation forbids unknown operators


def _empty_result(ruleset: RulesetSpec, snapshot: FantasyMatchSnapshot) -> FantasyResult:
    result = FantasyResult(
        match_id=snapshot.match.match_id,
        ruleset_id=ruleset.ruleset_id,
        version=ruleset.version,
    )
    return FantasyResult(
        match_id=result.match_id,
        ruleset_id=result.ruleset_id,
        version=result.version,
        players=(),
        unsupported_rules=(),
        is_partial=False,
        digest=result_digest(result),
    )


def compute(ruleset: RulesetSpec, snapshot: FantasyMatchSnapshot) -> FantasyResult:
    """Compute fantasy points for every player in the snapshot under the ruleset."""
    validate_ruleset(ruleset)
    policies = ruleset.policies

    # -- policy preprocessing ------------------------------------------------
    if snapshot.match.result_type in NO_CONTEST_RESULT_TYPES and policies.abandoned == "no_points":
        return _empty_result(ruleset, snapshot)
    if snapshot.reduced_overs and policies.reduced_overs == "exclude":
        return _empty_result(ruleset, snapshot)

    lines = [
        line
        for line in snapshot.player_lines
        if not (line.is_super_over and policies.super_over == "exclude")
        and not (line.is_substitute and policies.substitutes == "exclude")
    ]

    # -- availability split (data-requirements registry) ----------------------
    unsupported: list[str] = []
    available: list[PointRuleSpec] = []
    for rule in ruleset.rules:
        if _rule_available(rule, snapshot.coverage):
            available.append(rule)
        else:
            unsupported.append(rule.metric)

    # -- per-player evaluation ------------------------------------------------
    players: list[PlayerPoints] = []
    for line in lines:
        components: list[ComponentPoints] = []
        for rule in available:
            points = _rule_points(rule, rule.metric, line, policies)
            if points is None or points == 0:
                continue
            value = _metric_value(rule.metric, line, policies)
            components.append(
                ComponentPoints(metric=rule.metric, points=points, detail={"metric_value": value})
            )
        total = q2(sum(c.points for c in components))
        players.append(
            PlayerPoints(player_id=line.player_id, total=total, components=tuple(components))
        )

    result = FantasyResult(
        match_id=snapshot.match.match_id,
        ruleset_id=ruleset.ruleset_id,
        version=ruleset.version,
        players=tuple(sorted(players, key=lambda p: p.player_id)),
        unsupported_rules=tuple(sorted(set(unsupported))),
        is_partial=bool(unsupported),
    )
    return FantasyResult(
        match_id=result.match_id,
        ruleset_id=result.ruleset_id,
        version=result.version,
        players=result.players,
        unsupported_rules=result.unsupported_rules,
        is_partial=result.is_partial,
        digest=result_digest(result),
    )
