"""Rule vocabulary + validation (Task 2): the closed set of metrics,
operators, scopes, tier ordering, qualifiers and policies, and the precise
error types each violation raises.

Every rejected class is checked with a precise ``RulesetValidationError``
subclass — the engine must never silently accept an unknown metric, a
contradictory pair of rules, or a malformed parameter.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.fantasy.engine import (
    ContradictoryRulesError,
    InvalidOperatorError,
    InvalidParamError,
    InvalidPolicyError,
    InvalidScopeError,
    InvalidTierError,
    MissingQualifierError,
    PointRuleSpec,
    RulesetPolicies,
    RulesetSpec,
    UnknownMetricError,
    point_rule,
    validate_ruleset,
)


def _spec(*rules, **policy_kwargs) -> RulesetSpec:
    return RulesetSpec(
        ruleset_id="r",
        version=1,
        rules=tuple(rules),
        policies=RulesetPolicies(**policy_kwargs),
    )


# --------------------------------------------------------------------------- #
# unknown metric / operator / scope
# --------------------------------------------------------------------------- #


def test_unknown_metric_rejected() -> None:
    rule = point_rule("runs", "per_unit", "1.00", "batting")  # valid base
    # Rebuild the same rule shape with an unknown metric key.
    bad = PointRuleSpec(
        metric="boundaries",
        operator=rule.operator,
        points=rule.points,
        scope=rule.scope,
    )
    with pytest.raises(UnknownMetricError):
        validate_ruleset(_spec(bad))


def test_unknown_operator_rejected() -> None:
    with pytest.raises(InvalidOperatorError):
        point_rule("runs", "multiplier", "1.00", "batting")


def test_operator_kind_mismatch_rejected() -> None:
    # 'flag' is only legal for boolean metrics; 'tiered' only for numeric.
    with pytest.raises(InvalidOperatorError):
        point_rule("duck", "per_unit", "1.00", "batting")  # duck is boolean
    with pytest.raises(InvalidOperatorError):
        point_rule("playing_xi", "tiered", "1.00", "participation")


def test_unknown_scope_rejected() -> None:
    with pytest.raises(InvalidScopeError):
        point_rule("runs", "per_unit", "1.00", "coaching")


def test_scope_metric_mismatch_rejected() -> None:
    # 'runs' is a batting metric; declaring it bowling is contradictory.
    with pytest.raises(InvalidScopeError):
        point_rule("runs", "per_unit", "1.00", "bowling")


# --------------------------------------------------------------------------- #
# per_unit params
# --------------------------------------------------------------------------- #


def test_per_unit_unit_must_be_positive() -> None:
    with pytest.raises(InvalidParamError):
        point_rule("runs", "per_unit", "1.00", "batting", unit=0)


# --------------------------------------------------------------------------- #
# threshold_bonus params + contradictions
# --------------------------------------------------------------------------- #


def test_threshold_bonus_requires_min_value() -> None:
    with pytest.raises(InvalidParamError):
        point_rule("run_milestone", "threshold_bonus", "20.00", "batting")


def test_threshold_bonus_negative_min_value_rejected() -> None:
    with pytest.raises(InvalidParamError):
        point_rule("run_milestone", "threshold_bonus", "20.00", "batting", min_value=-1)


def test_threshold_bonus_max_below_min_rejected() -> None:
    with pytest.raises(InvalidParamError):
        point_rule(
            "run_milestone",
            "threshold_bonus",
            "20.00",
            "batting",
            min_value=50,
            max_value=40,
        )


def test_duplicate_threshold_rejected() -> None:
    """Two threshold_bonus rules at the same threshold are contradictory."""
    ruleset = _spec(
        point_rule("run_milestone", "threshold_bonus", "20.00", "batting", min_value=50),
        point_rule("run_milestone", "threshold_bonus", "30.00", "batting", min_value=50),
    )
    with pytest.raises(ContradictoryRulesError):
        validate_ruleset(ruleset)


def test_distinct_thresholds_allowed() -> None:
    ruleset = _spec(
        point_rule("run_milestone", "threshold_bonus", "20.00", "batting", min_value=50),
        point_rule("run_milestone", "threshold_bonus", "40.00", "batting", min_value=100),
    )
    validate_ruleset(ruleset)  # no raise


# --------------------------------------------------------------------------- #
# tiered params
# --------------------------------------------------------------------------- #


def test_tiered_requires_tiers() -> None:
    with pytest.raises(InvalidTierError):
        point_rule(
            "wicket_haul",
            "tiered",
            "0.00",
            "bowling",
            tier_mode="highest_only",
        )


def test_tiered_requires_tier_mode() -> None:
    with pytest.raises(InvalidTierError):
        point_rule(
            "wicket_haul",
            "tiered",
            "0.00",
            "bowling",
            tiers=[(3, "20.00")],
        )


def test_tiered_rejects_unknown_mode() -> None:
    with pytest.raises(InvalidTierError):
        point_rule(
            "wicket_haul",
            "tiered",
            "0.00",
            "bowling",
            tiers=[(3, "20.00")],
            tier_mode="best",
        )


def test_tiered_negative_points_rejected() -> None:
    with pytest.raises(InvalidTierError):
        point_rule(
            "wicket_haul",
            "tiered",
            "0.00",
            "bowling",
            tiers=[(3, "-20.00")],
            tier_mode="highest_only",
        )


def test_tiered_duplicate_threshold_rejected() -> None:
    with pytest.raises(InvalidTierError):
        point_rule(
            "wicket_haul",
            "tiered",
            "0.00",
            "bowling",
            tiers=[(3, "20.00"), (3, "30.00")],
            tier_mode="highest_only",
        )


def test_tiered_out_of_order_rejected() -> None:
    """Count/SR tiers must be strictly increasing (weakest first)."""
    with pytest.raises(InvalidTierError):
        point_rule(
            "wicket_haul",
            "tiered",
            "0.00",
            "bowling",
            tiers=[(5, "30.00"), (3, "20.00")],
            tier_mode="highest_only",
        )


def test_economy_tiers_must_decrease() -> None:
    """Lower economy is better: tiers must run highest threshold first."""
    # Valid: 6 then 4 (easiest -> hardest).
    validate_ruleset(
        _spec(
            point_rule(
                "economy_band",
                "tiered",
                "0.00",
                "bowling",
                tiers=[(6, "10.00"), (4, "20.00")],
                tier_mode="highest_only",
                min_overs=4,
            )
        )
    )
    # Invalid: 4 then 6 (hardest first).
    with pytest.raises(InvalidTierError):
        point_rule(
            "economy_band",
            "tiered",
            "0.00",
            "bowling",
            tiers=[(4, "20.00"), (6, "10.00")],
            tier_mode="highest_only",
            min_overs=4,
        )


# --------------------------------------------------------------------------- #
# qualifiers
# --------------------------------------------------------------------------- #


def test_strike_rate_requires_min_balls() -> None:
    with pytest.raises(MissingQualifierError):
        point_rule(
            "strike_rate_band",
            "tiered",
            "0.00",
            "batting",
            tiers=[(100, "10.00")],
            tier_mode="highest_only",
        )


def test_economy_requires_min_overs() -> None:
    with pytest.raises(MissingQualifierError):
        point_rule(
            "economy_band",
            "tiered",
            "0.00",
            "bowling",
            tiers=[(6, "10.00")],
            tier_mode="highest_only",
        )


# --------------------------------------------------------------------------- #
# flag / capped
# --------------------------------------------------------------------------- #


def test_flag_takes_no_numeric_params() -> None:
    with pytest.raises(InvalidParamError):
        point_rule("playing_xi", "flag", "4.00", "participation", min_value=1)


def test_capped_requires_positive_cap() -> None:
    with pytest.raises(InvalidParamError):
        point_rule("runs", "capped", "1.00", "batting")
    with pytest.raises(InvalidParamError):
        point_rule("runs", "capped", "1.00", "batting", cap_value="0.00")
    with pytest.raises(InvalidParamError):
        point_rule("runs", "capped", "1.00", "batting", cap_value="-5.00")


def test_capped_negative_points_rejected() -> None:
    """The only operator that forbids negative points (the cap bounds accrual)."""
    with pytest.raises(InvalidParamError):
        point_rule("runs", "capped", "-1.00", "batting", cap_value="50.00")


def test_negative_points_allowed_for_duck_penalty() -> None:
    """Non-capped negative points (duck penalties) are a supported class."""
    rule = point_rule("duck", "flag", "-5.00", "batting")
    validate_ruleset(_spec(rule))  # no raise
    assert rule.points == Decimal("-5.00")


# --------------------------------------------------------------------------- #
# same-operator contradictions
# --------------------------------------------------------------------------- #


def test_two_per_unit_rules_on_same_metric_rejected() -> None:
    ruleset = _spec(
        point_rule("runs", "per_unit", "1.00", "batting"),
        point_rule("runs", "per_unit", "2.00", "batting"),
    )
    with pytest.raises(ContradictoryRulesError):
        validate_ruleset(ruleset)


def test_two_flag_rules_on_same_metric_rejected() -> None:
    ruleset = _spec(
        point_rule("playing_xi", "flag", "4.00", "participation"),
        point_rule("playing_xi", "flag", "6.00", "participation"),
    )
    with pytest.raises(ContradictoryRulesError):
        validate_ruleset(ruleset)


def test_capped_and_per_unit_on_same_metric_rejected() -> None:
    """``capped`` already includes the per-unit accrual, so the two on one
    metric would double-count the same value."""
    ruleset = _spec(
        point_rule("runs", "per_unit", "1.00", "batting"),
        point_rule("runs", "capped", "1.00", "batting", cap_value="100.00"),
    )
    with pytest.raises(ContradictoryRulesError):
        validate_ruleset(ruleset)


def test_per_unit_bonus_composition_allowed() -> None:
    """per_unit accrual + a threshold_bonus milestone on the *same metric* are
    different value semantics (each run vs crossing a milestone) and coexist."""
    ruleset = _spec(
        point_rule("runs", "per_unit", "1.00", "batting"),
        point_rule("run_milestone", "threshold_bonus", "20.00", "batting", min_value=50),
    )
    validate_ruleset(ruleset)  # no raise


# --------------------------------------------------------------------------- #
# policies
# --------------------------------------------------------------------------- #


def test_invalid_policy_value_rejected() -> None:
    with pytest.raises(InvalidPolicyError):
        validate_ruleset(
            _spec(point_rule("runs", "per_unit", "1.00", "batting"), super_over="sometimes")
        )


def test_invalid_retirement_policy_rejected() -> None:
    with pytest.raises(InvalidPolicyError):
        validate_ruleset(
            _spec(point_rule("runs", "per_unit", "1.00", "batting"), retirement_hurt="maybe")
        )


def test_default_policies_are_valid() -> None:
    validate_ruleset(_spec(point_rule("runs", "per_unit", "1.00", "batting")))  # no raise
