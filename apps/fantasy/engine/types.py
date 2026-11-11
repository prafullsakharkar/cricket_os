"""Frozen, typed dataclasses for the pure fantasy engine (Task 2).

Every input to :func:`compute` is a frozen dataclass so the engine is a pure
function of its inputs: no mutation, no I/O, no clock, no randomness. The same
(ruleset spec, snapshot) always reproduces byte-identical points.

Decimal discipline (plan decision 9 / tension 3): every point value is a
``Decimal``. Per-rule results are quantized to 2 decimal places with
``ROUND_HALF_UP``; totals are the sum of the quantized components, quantized
again. No float appears anywhere in compute or storage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any


@dataclass(frozen=True)
class SnapshotCoverage:
    """Which data classes the snapshot carries (data-availability, decision 8).

    A rule is computable only when its metric's declared inputs exist in the
    snapshot for this match; otherwise it is skipped, reported in
    ``FantasyResult.unsupported_rules``, and the result is flagged partial —
    never guessed.
    """

    has_ball_data: bool = False
    has_dismissal_kinds: bool = False
    has_fielder_credit_types: bool = False

    def __bool__(self) -> bool:
        # A snapshot with no coverage at all is a degenerate empty snapshot.
        return any((self.has_ball_data, self.has_dismissal_kinds, self.has_fielder_credit_types))


@dataclass(frozen=True)
class PlayerLine:
    """One player's match line inside a snapshot.

    ``None`` means "not present in this match's data" (the builder records the
    absence); the engine's per-metric value extraction treats an absent field
    as 0/False once the metric's availability condition holds at the snapshot
    level. ``is_super_over`` / ``is_substitute`` / ``retired_hurt`` are the
    match-situation flags the per-ruleset policies read.
    """

    player_id: str
    is_playing_xi: bool = False
    did_bat: bool = False
    did_bowl: bool = False
    did_field: bool = False
    # -- batting -----------------------------------------------------------
    runs: int = 0
    balls_faced: int | None = None
    fours: int = 0
    sixes: int = 0
    dismissal_kind: str | None = None
    # -- bowling -----------------------------------------------------------
    balls_bowled: int | None = None
    wickets: int = 0
    maidens: int | None = None
    runs_conceded: int | None = None
    dots_bowled: int | None = None
    bowled_lbw_wickets: int = 0
    hat_trick: bool = False
    # -- fielding ----------------------------------------------------------
    catches: int = 0
    stumpings: int = 0
    run_outs_total: int = 0
    run_outs_direct: int | None = None
    run_outs_indirect: int | None = None
    # -- participation / awards -------------------------------------------
    player_of_match: bool = False
    # -- match-situation flags (policy inputs) ----------------------------
    is_super_over: bool = False
    is_substitute: bool = False
    retired_hurt: bool = False


@dataclass(frozen=True)
class MatchContext:
    """Match facts the ruleset policies and the API labels read."""

    match_id: str
    format: str | None = None
    #: Result type as recorded (e.g. "NORMAL", "ABANDONED", "NO_RESULT", "TIE").
    result_type: str | None = None
    series_id: str | None = None


@dataclass(frozen=True)
class FantasyMatchSnapshot:
    """Immutable snapshot of one match's statistics (Task 3 builder).

    ``digest`` is the canonical snapshot digest (``digest.snapshot_digest``),
    filled by the builder; ``compare=False`` so the digest never participates
    in dataclass equality.
    """

    match: MatchContext
    coverage: SnapshotCoverage
    player_lines: tuple[PlayerLine, ...] = ()
    super_over_played: bool = False
    reduced_overs: bool = False
    digest: str = field(default="", compare=False)


@dataclass(frozen=True)
class TierSpec:
    """One tier of a ``tiered`` rule: award ``points`` when value >= min_value."""

    min_value: int
    points: Decimal


@dataclass(frozen=True)
class PointRuleSpec:
    """One declarative rule (vocabulary: metrics_registry.py + rules.py).

    Operators: ``per_unit`` | ``threshold_bonus`` | ``tiered`` | ``flag`` |
    ``capped``. Scope: ``batting`` | ``bowling`` | ``fielding`` |
    ``participation``. Rate-bonus rules carry ``min_balls`` / ``min_overs``
    qualifiers (required by validation for strike-rate/economy bands).
    """

    metric: str
    operator: str
    points: Decimal
    scope: str
    unit: int | None = None
    min_value: int | None = None
    max_value: int | None = None
    tiers: tuple[TierSpec, ...] = ()
    tier_mode: str | None = None
    min_balls: int | None = None
    min_overs: int | None = None
    cap_value: Decimal | None = None


@dataclass(frozen=True)
class RulesetPolicies:
    """Per-ruleset policy flags for match-situation handling (documented in
    FANTASY.md). Defaults are the sensible values the brief names."""

    #: super_over: "exclude" (default) | "include"
    super_over: str = "exclude"
    #: abandoned/no-result: "no_points" (default) | "compute"
    abandoned: str = "no_points"
    #: reduced-overs: "compute" (default) | "exclude"
    reduced_overs: str = "compute"
    #: retirement hurt: "not_out" (default) | "count_as_out"
    retirement_hurt: str = "not_out"
    #: substitutes/impact replacements: "exclude" (default) | "include"
    substitutes: str = "exclude"


@dataclass(frozen=True)
class RulesetSpec:
    """A frozen ruleset: identity + ordered rule set + policies."""

    ruleset_id: str
    version: int
    format_scope: str | None = None
    rules: tuple[PointRuleSpec, ...] = ()
    policies: RulesetPolicies = field(default_factory=RulesetPolicies)


@dataclass(frozen=True)
class ComponentPoints:
    """One rule's contribution to a player's total (breakdown entry)."""

    metric: str
    points: Decimal
    detail: Any = None


@dataclass(frozen=True)
class PlayerPoints:
    """A player's fantasy points with the component breakdown."""

    player_id: str
    total: Decimal
    components: tuple[ComponentPoints, ...] = ()


@dataclass(frozen=True)
class FantasyResult:
    """The engine's output for one (ruleset, snapshot) computation."""

    match_id: str
    ruleset_id: str
    version: int
    players: tuple[PlayerPoints, ...] = ()
    #: Rules skipped because the snapshot lacks their inputs (never guessed).
    unsupported_rules: tuple[str, ...] = ()
    #: True when any rule was unavailable (or the snapshot is summary-only).
    is_partial: bool = False
    #: Deterministic digest of the whole result (digest.result_digest).
    digest: str = field(default="", compare=False)


#: Dismissal kinds that count as "out" for the ``duck`` metric. Retired hurt
#: is deliberately NOT here: a retirement-hurt batter is not out (the policy
#: flag ``retirement_hurt`` can override this for a specific ruleset).
OUT_DISMISSAL_KINDS: frozenset[str] = frozenset(
    {
        "bowled",
        "lbw",
        "caught",
        "caught_behind",
        "stumped",
        "run_out",
        "hit_wicket",
        "handled_ball",
        "hit_the_ball_twice",
        "obstructing_the_field",
        "timed_out",
        "retired_out",
    }
)

#: Result types that mean the match produced no contest (abandoned / no result).
NO_CONTEST_RESULT_TYPES: frozenset[str] = frozenset({"ABANDONED", "NO_RESULT"})

#: Rounding: 2 decimal places, ROUND_HALF_UP (documented quantization).
POINTS_QUANT = Decimal("0.01")


def q2(value: Decimal | int) -> Decimal:
    """Quantize a Decimal to 2dp with ROUND_HALF_UP (the only rounding mode).

    ``int`` inputs (e.g. ``sum()`` of an empty component list) are coerced to
    Decimal first; floats are deliberately not accepted — no float may enter
    the point arithmetic.
    """
    return Decimal(value).quantize(POINTS_QUANT, rounding=ROUND_HALF_UP)
