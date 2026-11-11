"""Pure fantasy compute engine (Phase H, Task 2).

The engine is a **pure function of (ruleset spec, match snapshot)**: no
Django, no ORM, no clock, no randomness, no network — the same inputs always
produce byte-identical points. Import from this package (not individual
modules) so the public surface stays explicit.

Purity is enforced by an import-linter contract (``apps.fantasy.engine`` may
import no other first-party package) and a Django-blocked import test, mirror
of the ``scoring`` engine precedent.
"""

from __future__ import annotations

from apps.fantasy.engine.compute import compute
from apps.fantasy.engine.digest import content_hash, result_digest, snapshot_digest
from apps.fantasy.engine.metrics_registry import data_requirements, metric_spec
from apps.fantasy.engine.rules import (
    ContradictoryRulesError,
    InvalidOperatorError,
    InvalidParamError,
    InvalidPolicyError,
    InvalidScopeError,
    InvalidTierError,
    MissingQualifierError,
    RulesetValidationError,
    UnknownMetricError,
    point_rule,
    validate_ruleset,
)
from apps.fantasy.engine.types import (
    OUT_DISMISSAL_KINDS,
    ComponentPoints,
    FantasyMatchSnapshot,
    FantasyResult,
    MatchContext,
    PlayerLine,
    PlayerPoints,
    PointRuleSpec,
    RulesetPolicies,
    RulesetSpec,
    SnapshotCoverage,
    TierSpec,
    q2,
)

__all__ = [
    "OUT_DISMISSAL_KINDS",
    "ComponentPoints",
    "ContradictoryRulesError",
    "FantasyMatchSnapshot",
    "FantasyResult",
    "InvalidOperatorError",
    "InvalidParamError",
    "InvalidPolicyError",
    "InvalidScopeError",
    "InvalidTierError",
    "MatchContext",
    "MissingQualifierError",
    "PlayerLine",
    "PlayerPoints",
    "PointRuleSpec",
    "RulesetPolicies",
    "RulesetSpec",
    "RulesetValidationError",
    "SnapshotCoverage",
    "TierSpec",
    "UnknownMetricError",
    "compute",
    "content_hash",
    "data_requirements",
    "metric_spec",
    "point_rule",
    "q2",
    "result_digest",
    "snapshot_digest",
    "validate_ruleset",
]
