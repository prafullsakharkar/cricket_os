"""Read-optimised team-domain queries (six-layer rule: no service imports)."""

from __future__ import annotations

from apps.teams.selectors.memberships import list_players_of_team
from apps.teams.selectors.teams import TEAM_ORDERINGS, get_team, list_teams

__all__ = ["TEAM_ORDERINGS", "get_team", "list_players_of_team", "list_teams"]
