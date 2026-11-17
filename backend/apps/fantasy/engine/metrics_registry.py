"""Closed registry of metric keys + the data-requirements table (Task 2).

Every metric key a ruleset may reference is declared here with:

- ``scope``: which player dimension it belongs to (batting/bowling/fielding/
  participation) — a rule's scope must match its metric's scope.
- ``kind``: ``numeric`` (a count/rate) or ``boolean`` (a flag) — determines
  which operators are legal.
- ``reads``: the ``PlayerLine`` field names the metric consumes.
- availability conditions: the ``SnapshotCoverage`` flags that must hold for
  the metric to be computable from this match's snapshot. A metric whose
  inputs the snapshot lacks is skipped by ``compute`` and reported in
  ``unsupported_rules`` — never guessed (plan decision 8 / tension 2).

This registry is pure data so it can be rendered into the docs data-requirements
table (FANTASY.md) and drives validation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricSpec:
    key: str
    scope: str
    kind: str  # "numeric" | "boolean"
    reads: tuple[str, ...]
    needs_ball_data: bool = False
    needs_dismissal_kinds: bool = False
    #: Requires the match's fielding counts (catches/stumpings/run-outs).
    needs_fielding_data: bool = False
    #: Requires direct-vs-relay run-out attribution (delivery-level data).
    needs_fielder_credit_types: bool = False
    #: Whether a *higher* or a *lower* value is better for tiered rules
    #: ("higher" = counts/strike-rate; "lower" = economy). Tier lists must be
    #: ordered from the easiest to the hardest threshold, so "lower"-is-better
    #: metrics list their thresholds in strictly decreasing order.
    better_is: str = "higher"


BATTING = "batting"
BOWLING = "bowling"
FIELDING = "fielding"
PARTICIPATION = "participation"

#: The closed metric registry — the only metric keys a ruleset may use.
METRICS: dict[str, MetricSpec] = {
    "playing_xi": MetricSpec("playing_xi", PARTICIPATION, "boolean", ("is_playing_xi",)),
    "runs": MetricSpec("runs", BATTING, "numeric", ("runs",)),
    "balls": MetricSpec("balls", BATTING, "numeric", ("balls_faced",), needs_ball_data=True),
    "fours": MetricSpec("fours", BATTING, "numeric", ("fours",)),
    "sixes": MetricSpec("sixes", BATTING, "numeric", ("sixes",)),
    "duck": MetricSpec(
        "duck", BATTING, "boolean", ("runs", "dismissal_kind"), needs_dismissal_kinds=True
    ),
    "run_milestone": MetricSpec("run_milestone", BATTING, "numeric", ("runs",)),
    "strike_rate_band": MetricSpec(
        "strike_rate_band", BATTING, "numeric", ("runs", "balls_faced"), needs_ball_data=True
    ),
    "wicket": MetricSpec("wicket", BOWLING, "numeric", ("wickets",)),
    "bowled_lbw_bonus": MetricSpec(
        "bowled_lbw_bonus",
        BOWLING,
        "numeric",
        ("bowled_lbw_wickets",),
        needs_dismissal_kinds=True,
    ),
    "wicket_haul": MetricSpec("wicket_haul", BOWLING, "numeric", ("wickets",)),
    "maiden": MetricSpec("maiden", BOWLING, "numeric", ("maidens",), needs_ball_data=True),
    "economy_band": MetricSpec(
        "economy_band",
        BOWLING,
        "numeric",
        ("runs_conceded", "balls_bowled"),
        needs_ball_data=True,
        better_is="lower",
    ),
    "dot_ball": MetricSpec("dot_ball", BOWLING, "numeric", ("dots_bowled",), needs_ball_data=True),
    "catch": MetricSpec("catch", FIELDING, "numeric", ("catches",), needs_fielding_data=True),
    "multi_catch_bonus": MetricSpec(
        "multi_catch_bonus", FIELDING, "numeric", ("catches",), needs_fielding_data=True
    ),
    "stumping": MetricSpec(
        "stumping", FIELDING, "numeric", ("stumpings",), needs_fielding_data=True
    ),
    "run_out": MetricSpec(
        "run_out", FIELDING, "numeric", ("run_outs_total",), needs_fielding_data=True
    ),
    "run_out_direct": MetricSpec(
        "run_out_direct",
        FIELDING,
        "numeric",
        ("run_outs_direct",),
        needs_fielding_data=True,
        needs_fielder_credit_types=True,
    ),
    "run_out_indirect": MetricSpec(
        "run_out_indirect",
        FIELDING,
        "numeric",
        ("run_outs_indirect",),
        needs_fielding_data=True,
        needs_fielder_credit_types=True,
    ),
    "player_of_match": MetricSpec(
        "player_of_match", PARTICIPATION, "boolean", ("player_of_match",)
    ),
    "hat_trick": MetricSpec("hat_trick", BOWLING, "boolean", ("hat_trick",)),
}

#: Operators by metric kind (validation).
NUMERIC_OPERATORS = frozenset({"per_unit", "threshold_bonus", "tiered", "capped"})
BOOLEAN_OPERATORS = frozenset({"flag"})

#: Rate-band metrics that must declare a ball/over qualifier (validation).
REQUIRED_MIN_BALLS = frozenset({"strike_rate_band"})
REQUIRED_MIN_OVERS = frozenset({"economy_band"})

#: Metrics whose numeric value is a rate (computed from reads), not a count.
RATE_METRICS = frozenset({"strike_rate_band", "economy_band"})

#: Scopes a rule may declare.
SCOPES = frozenset({BATTING, BOWLING, FIELDING, PARTICIPATION})


def metric_spec(key: str) -> MetricSpec | None:
    return METRICS.get(key)


def data_requirements(key: str) -> MetricSpec | None:
    """Return the declared inputs/availability for a metric (registry lookup)."""
    return METRICS.get(key)
