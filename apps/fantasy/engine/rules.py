"""Rule vocabulary + validation (Task 2). Rules are data, not code.

A rule is a validated tuple from a closed vocabulary — no ``eval``, no
expression strings:

- ``metric``: a key from :data:`metrics_registry.METRICS`.
- ``operator``: ``per_unit`` | ``threshold_bonus`` | ``tiered`` | ``flag`` |
  ``capped``.
- ``scope``: batting | bowling | fielding | participation.
- params: ``points``, ``unit`` (per_unit), ``min_value``/``max_value``
  (threshold_bonus), ``tiers`` + ``tier_mode`` (tiered), ``cap_value``
  (capped), ``min_balls``/``min_overs`` (rate-band qualifiers).

:func:`validate_ruleset` rejects unknown metrics, invalid operators/scopes,
non-monotonic or overlapping tiers, contradictory rules (duplicates, two
rules of the same operator on the same metric/scope), negative points, and
missing qualifiers — each with a precise error type.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from apps.fantasy.engine.metrics_registry import (
    BOOLEAN_OPERATORS,
    NUMERIC_OPERATORS,
    RATE_METRICS,
    REQUIRED_MIN_BALLS,
    REQUIRED_MIN_OVERS,
    SCOPES,
    metric_spec,
)
from apps.fantasy.engine.types import (
    PointRuleSpec,
    RulesetPolicies,
    RulesetSpec,
    TierSpec,
)


class RulesetValidationError(ValueError):
    """Base class for precise ruleset validation failures."""


class UnknownMetricError(RulesetValidationError):
    """A rule references a metric key outside the closed registry."""


class InvalidOperatorError(RulesetValidationError):
    """The operator is unknown or not legal for the metric's kind."""


class InvalidScopeError(RulesetValidationError):
    """The scope does not match the metric's declared scope."""


class InvalidTierError(RulesetValidationError):
    """Tiers are empty, overlapping, non-monotonic, or carry negative points."""


class MissingQualifierError(RulesetValidationError):
    """A rate-band rule lacks its required min_balls/min_overs qualifier."""


class ContradictoryRulesError(RulesetValidationError):
    """Duplicate rules or two rules of the same operator on one metric/scope."""


class InvalidPolicyError(RulesetValidationError):
    """A policy flag has an unknown key or value."""


class InvalidParamError(RulesetValidationError):
    """A rule parameter is missing, negative, or otherwise malformed."""


VALID_OPERATORS = frozenset(NUMERIC_OPERATORS | BOOLEAN_OPERATORS)
TIER_MODES = frozenset({"highest_only", "cumulative"})

#: Policy key -> allowed values (validation).
POLICY_VALUES: dict[str, frozenset[str]] = {
    "super_over": frozenset({"exclude", "include"}),
    "abandoned": frozenset({"no_points", "compute"}),
    "reduced_overs": frozenset({"compute", "exclude"}),
    "retirement_hurt": frozenset({"not_out", "count_as_out"}),
    "substitutes": frozenset({"exclude", "include"}),
}


def _validate_rule(rule: PointRuleSpec) -> None:
    spec = metric_spec(rule.metric)
    if spec is None:
        raise UnknownMetricError(f"unknown metric: {rule.metric!r}")
    if rule.operator not in VALID_OPERATORS:
        raise InvalidOperatorError(f"unknown operator {rule.operator!r} for metric {rule.metric!r}")
    if rule.scope not in SCOPES:
        raise InvalidScopeError(f"unknown scope: {rule.scope!r}")
    if rule.scope != spec.scope:
        raise InvalidScopeError(
            f"scope {rule.scope!r} does not match metric {rule.metric!r}'s scope {spec.scope!r}"
        )
    if rule.operator not in (NUMERIC_OPERATORS if spec.kind == "numeric" else BOOLEAN_OPERATORS):
        raise InvalidOperatorError(
            f"operator {rule.operator!r} is not legal for {spec.kind} metric {rule.metric!r}"
        )
    # Negative points are allowed (e.g. duck penalties) — only ``capped``
    # requires non-negative points because the cap bounds positive accrual.
    if rule.points < 0 and rule.operator == "capped":
        raise InvalidParamError(f"capped rule {rule.metric!r} needs non-negative points")

    if rule.operator == "per_unit":
        if rule.unit is not None and rule.unit < 1:
            raise InvalidParamError(f"per_unit unit must be >= 1 on {rule.metric!r}")
    elif rule.operator == "threshold_bonus":
        if rule.min_value is None:
            raise InvalidParamError(f"threshold_bonus needs min_value on {rule.metric!r}")
        if rule.min_value < 0:
            raise InvalidParamError(f"min_value must be >= 0 on {rule.metric!r}")
        if rule.max_value is not None and rule.max_value < rule.min_value:
            raise InvalidParamError(f"max_value must be >= min_value on {rule.metric!r}")
    elif rule.operator == "tiered":
        if not rule.tiers:
            raise InvalidTierError(f"tiered rule {rule.metric!r} needs at least one tier")
        if rule.tier_mode not in TIER_MODES:
            raise InvalidTierError(
                f"tiered rule {rule.metric!r} needs tier_mode in {sorted(TIER_MODES)}"
            )
        # Tier lists run from the easiest to the hardest threshold. For
        # "higher"-is-better metrics (counts, strike rate) that is strictly
        # increasing; for "lower"-is-better metrics (economy) it is strictly
        # decreasing. ``reached[-1]`` in compute then always means "the hardest
        # tier reached".
        increasing = spec.better_is == "higher"
        previous = None
        seen: set[int] = set()
        for tier in rule.tiers:
            if tier.points < 0:
                raise InvalidTierError(f"negative tier points on {rule.metric!r}")
            if tier.min_value in seen:
                raise InvalidTierError(f"duplicate tier min_value on {rule.metric!r}")
            seen.add(tier.min_value)
            if previous is not None:
                if increasing and tier.min_value <= previous:
                    raise InvalidTierError(f"tiers must be strictly increasing on {rule.metric!r}")
                if not increasing and tier.min_value >= previous:
                    raise InvalidTierError(
                        f"tiers must be strictly decreasing on {rule.metric!r} "
                        f"(lower {rule.metric!r} is better)"
                    )
            previous = tier.min_value
    elif rule.operator == "flag":
        # A flag rule carries no numeric params; the metric value decides.
        if rule.min_value is not None or rule.cap_value is not None:
            raise InvalidParamError(f"flag rule {rule.metric!r} takes no numeric params")
    elif rule.operator == "capped":
        if rule.cap_value is None or Decimal(rule.cap_value) <= 0:
            raise InvalidParamError(f"capped rule {rule.metric!r} needs a positive cap_value")
        if rule.unit is not None and rule.unit < 1:
            raise InvalidParamError(f"capped unit must be >= 1 on {rule.metric!r}")

    if rule.metric in REQUIRED_MIN_BALLS and rule.min_balls is None:
        raise MissingQualifierError(f"{rule.metric!r} requires min_balls")
    if rule.metric in REQUIRED_MIN_OVERS and rule.min_overs is None:
        raise MissingQualifierError(f"{rule.metric!r} requires min_overs")


def _validate_policies(policies: RulesetPolicies) -> None:
    for key, value in (
        ("super_over", policies.super_over),
        ("abandoned", policies.abandoned),
        ("reduced_overs", policies.reduced_overs),
        ("retirement_hurt", policies.retirement_hurt),
        ("substitutes", policies.substitutes),
    ):
        if value not in POLICY_VALUES[key]:
            raise InvalidPolicyError(
                f"policy {key!r} has invalid value {value!r}; allowed {sorted(POLICY_VALUES[key])}"
            )


def validate_ruleset(ruleset: RulesetSpec) -> None:
    """Validate a whole ruleset; raises the precise error on the first fault."""
    _validate_policies(ruleset.policies)
    seen: dict[tuple[str, str, str], list[PointRuleSpec]] = {}
    for rule in ruleset.rules:
        _validate_rule(rule)
        key = (rule.metric, rule.scope, rule.operator)
        group = seen.setdefault(key, [])
        if group:
            # Two threshold_bonus rules on the same metric/scope are allowed
            # (multiple milestones), but only if their thresholds differ.
            if rule.operator == "threshold_bonus":
                if any(other.min_value == rule.min_value for other in group):
                    raise ContradictoryRulesError(
                        f"duplicate threshold_bonus on {rule.metric!r} at min_value "
                        f"{rule.min_value}"
                    )
            else:
                raise ContradictoryRulesError(
                    f"two {rule.operator} rules for {rule.metric!r}/{rule.scope!r}"
                )
        group.append(rule)
    # A rate metric must not appear with a non-rate operator (contradiction).
    for rule in ruleset.rules:
        if rule.metric in RATE_METRICS and rule.operator in ("per_unit", "threshold_bonus"):
            raise ContradictoryRulesError(
                f"rate metric {rule.metric!r} must use the tiered operator"
            )
    # ``capped`` is the "points per unit, capped at cap_value" operator: it
    # replaces per_unit on the same value source, so the two must not coexist
    # on one metric (they would double-count the same value).
    operators_by_metric: dict[tuple[str, str], set[str]] = {}
    for rule in ruleset.rules:
        operators_by_metric.setdefault((rule.metric, rule.scope), set()).add(rule.operator)
    for (metric, scope), operators in operators_by_metric.items():
        if "per_unit" in operators and "capped" in operators:
            raise ContradictoryRulesError(
                f"per_unit and capped both target {metric!r}/{scope!r} (capped "
                f"already includes the per-unit accrual)"
            )


def point_rule(
    metric: str,
    operator: str,
    points: Decimal | int | str,
    scope: str,
    *,
    unit: int | None = None,
    min_value: int | None = None,
    max_value: int | None = None,
    tiers: Sequence[tuple[int, Decimal | int | str] | TierSpec] = (),
    tier_mode: str | None = None,
    min_balls: int | None = None,
    min_overs: int | None = None,
    cap_value: Decimal | int | str | None = None,
) -> PointRuleSpec:
    """Small factory: build and validate one rule from scalar parts.

    ``tiers`` is a sequence of ``(min_value, points)`` tuples or ``TierSpec``
    instances. Money-typed params are normalized to ``Decimal`` here so
    validation and compute never see strings.
    """
    tier_specs: tuple[TierSpec, ...] = ()
    if tiers:
        tier_specs = tuple(
            t if isinstance(t, TierSpec) else TierSpec(min_value=int(t[0]), points=Decimal(t[1]))
            for t in tiers
        )
    rule = PointRuleSpec(
        metric=metric,
        operator=operator,
        points=Decimal(points),
        scope=scope,
        unit=unit,
        min_value=min_value,
        max_value=max_value,
        tiers=tier_specs,
        tier_mode=tier_mode,
        min_balls=min_balls,
        min_overs=min_overs,
        cap_value=Decimal(cap_value) if cap_value is not None else None,
    )
    validate_ruleset(RulesetSpec(ruleset_id="", version=1, rules=(rule,)))
    return rule
