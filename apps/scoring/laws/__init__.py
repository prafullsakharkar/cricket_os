"""Dependency-free rule functions (no ORM, no I/O, no clock)."""

from __future__ import annotations

from apps.scoring.laws.dismissals import (
    batter_appearances,
    batting_line,
    bowler_appearances,
    bowling_line,
    dismissal_for,
    is_bowler_wicket,
    is_team_wicket,
    wicket_count,
)
from apps.scoring.laws.extras import (
    batter_faces_ball,
    bowler_runs_charged,
    extras_breakdown,
    is_batter_dot,
    is_bowling_dot,
)
from apps.scoring.laws.overs import (
    balls_remaining,
    legal_balls,
    maidens_of,
    over_is_complete,
    overs_display,
    overs_of,
    total_balls,
)
from apps.scoring.laws.rates import (
    batting_average,
    boundary_pct,
    bowling_average,
    bowling_strike_rate,
    current_run_rate,
    dot_pct,
    economy,
    nrr_overs,
    overs_from_balls,
    required_run_rate,
    safe_div,
    strike_rate,
)

__all__ = [
    "balls_remaining",
    "batter_appearances",
    "batter_faces_ball",
    "batting_average",
    "batting_line",
    "boundary_pct",
    "bowler_appearances",
    "bowler_runs_charged",
    "bowling_average",
    "bowling_line",
    "bowling_strike_rate",
    "current_run_rate",
    "dismissal_for",
    "dot_pct",
    "economy",
    "extras_breakdown",
    "is_batter_dot",
    "is_bowler_wicket",
    "is_bowling_dot",
    "is_team_wicket",
    "legal_balls",
    "maidens_of",
    "nrr_overs",
    "over_is_complete",
    "overs_display",
    "overs_from_balls",
    "overs_of",
    "required_run_rate",
    "safe_div",
    "strike_rate",
    "total_balls",
    "wicket_count",
]
