"""Pure ratio helpers. Zero denominators always yield ``None`` (never 0/∞)."""

from __future__ import annotations

from apps.scoring.config import RuleConfig
from apps.scoring.errors import InvalidStatInputError


def safe_div(numerator: float, denominator: float) -> float | None:
    """Return ``numerator / denominator`` or ``None`` when undefined."""

    if denominator == 0:
        return None
    return numerator / denominator


def batting_average(runs: int, innings: int, not_outs: int) -> float | None:
    """Runs per dismissal; ``None`` when the batter was never dismissed."""

    if runs < 0 or innings < 0 or not_outs < 0:
        raise InvalidStatInputError("career inputs must be non-negative")
    if not_outs > innings:
        raise InvalidStatInputError("not_outs cannot exceed innings")
    return safe_div(runs, innings - not_outs)


def strike_rate(runs: int, balls: int) -> float | None:
    if balls < 0 or runs < 0:
        raise InvalidStatInputError("runs and balls must be non-negative")
    return safe_div(100.0 * runs, balls)


def bowling_average(runs_conceded: int, wickets: int) -> float | None:
    return safe_div(runs_conceded, wickets)


def bowling_strike_rate(legal_balls: int, wickets: int) -> float | None:
    return safe_div(legal_balls, wickets)


def economy(runs_conceded: int, legal_balls: int, config: RuleConfig) -> float | None:
    """Runs per ``config.balls_per_over``-ball over/set; ``None`` with no balls."""

    if legal_balls < 0:
        raise InvalidStatInputError("legal_balls must be non-negative")
    return safe_div(runs_conceded * config.balls_per_over, legal_balls)


def current_run_rate(runs: int, legal_balls: int, config: RuleConfig) -> float | None:
    return economy(runs, legal_balls, config)


def balls_remaining(total_balls: int | None, legal_balls: int) -> int | None:
    if total_balls is None:
        return None
    return max(total_balls - legal_balls, 0)


def required_run_rate(
    target: int,
    runs: int,
    total_balls: int | None,
    legal_balls: int,
    config: RuleConfig,
) -> float | None:
    """Runs per over required; ``None`` once no balls remain or quota is unknown."""

    remaining = balls_remaining(total_balls, legal_balls)
    if not remaining:
        return None
    return safe_div((target - runs) * config.balls_per_over, remaining)


def dot_pct(dots: int, balls: int) -> float | None:
    return safe_div(100.0 * dots, balls)


def boundary_pct(fours: int, sixes: int, balls: int) -> float | None:
    return safe_div(100.0 * (fours + sixes), balls)


def overs_from_balls(legal_balls: int, config: RuleConfig) -> float:
    return legal_balls / config.balls_per_over


def nrr_overs(
    legal_balls: int,
    *,
    all_out: bool,
    overs_limit: int | None,
    config: RuleConfig,
) -> float:
    """Overs charged for NRR: full quota when bowled out (E14)."""

    if all_out and config.nrr_full_quota_if_all_out and overs_limit is not None:
        return float(overs_limit)
    return overs_from_balls(legal_balls, config)
