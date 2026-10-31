"""Read-optimised player-domain queries (six-layer rule: no service imports)."""

from __future__ import annotations

from apps.players.selectors.players import (
    get_player,
    list_duplicate_candidates,
    list_players,
)

__all__ = ["get_player", "list_duplicate_candidates", "list_players"]
