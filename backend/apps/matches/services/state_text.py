"""Pure text formatters for the live scoreboard (API_SPECIFICATION §4.1).

Two human-readable fields are derived from engine outputs and lifecycle state:

* ``state_title`` — where the match is in its calendar/session, e.g.
  ``"Day 4 - Tea"`` or ``"Innings break"``;
* ``status_detail`` — the live competitive situation, e.g.
  ``"AUS lead by 50"``, ``"IND need 45 runs from 30 balls"``.

Everything here is pure (no ORM, no clock) so it is cheap to unit-test and can
be reused by the API layer without duplicating wording.
"""

from __future__ import annotations

from apps.core.enums import MatchStatus

#: Session labels a provider may report inside a day of play.
SESSIONS = ("Morning", "Afternoon", "Tea", "Evening")

_STATUS_DEFAULT_TITLE: dict[str, str] = {
    MatchStatus.SCHEDULED: "Scheduled",
    MatchStatus.TOSS: "Toss",
    MatchStatus.IN_PROGRESS: "In progress",
    MatchStatus.INNINGS_BREAK: "Innings break",
    MatchStatus.DRINKS: "Drinks",
    MatchStatus.RAIN_DELAY: "Rain delay",
    MatchStatus.BAD_LIGHT: "Bad light",
    MatchStatus.STUMPS: "Stumps",
    MatchStatus.DAY_BREAK: "Day break",
    MatchStatus.COMPLETED: "Completed",
    MatchStatus.ABANDONED: "Abandoned",
    MatchStatus.CANCELLED: "Cancelled",
    MatchStatus.FORFEITED: "Forfeited",
    MatchStatus.POSTPONED: "Postponed",
}


def state_title(
    status: str,
    *,
    day: int | None = None,
    session: str | None = None,
) -> str:
    """Compose ``"Day N - Session"`` when known, else the status label.

    Test matches are the only format with days; for limited-overs matches the
    status label is returned even if a day is supplied.
    """
    if day is not None and day > 0 and session:
        return f"Day {day} - {session}"
    if day is not None and day > 0:
        return f"Day {day}"
    return _STATUS_DEFAULT_TITLE.get(status, status.replace("_", " ").title())


def lead_detail(team_abbreviation: str, runs: int) -> str:
    """``"AUS lead by 50"`` / ``"AUS lead by 1 run"``."""
    unit = "run" if runs == 1 else "runs"
    return f"{team_abbreviation} lead by {runs} {unit}"


def trail_detail(team_abbreviation: str, runs: int) -> str:
    """``"AUS trail by 50"`` / ``"AUS trail by 1 run"``."""
    unit = "run" if runs == 1 else "runs"
    return f"{team_abbreviation} trail by {runs} {unit}"


def chase_detail(team_abbreviation: str, runs_needed: int, balls_left: int | None) -> str:
    """``"IND need 45 runs from 30 balls"`` (or ``"from 30 ball"``)."""
    if balls_left is None:
        return f"{team_abbreviation} need {runs_needed} runs"
    unit = "ball" if balls_left == 1 else "balls"
    return f"{team_abbreviation} need {runs_needed} runs from {balls_left} {unit}"


def all_out_detail(team_abbreviation: str, runs: int) -> str:
    """``"IND all out for 250"``."""
    return f"{team_abbreviation} all out for {runs}"


def target_detail(team_abbreviation: str, target: int) -> str:
    """``"IND target 251"``."""
    return f"{team_abbreviation} target {target}"


__all__ = [
    "SESSIONS",
    "all_out_detail",
    "chase_detail",
    "lead_detail",
    "state_title",
    "target_detail",
    "trail_detail",
]
