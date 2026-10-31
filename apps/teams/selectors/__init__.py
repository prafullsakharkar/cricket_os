"""Read-optimised team-domain queries (six-layer rule: no service imports)."""

from __future__ import annotations

from apps.teams.selectors.memberships import list_players_of_team
from apps.teams.selectors.teams import get_team, list_teams

__all__ = ["get_team", "list_players_of_team", "list_teams"]
