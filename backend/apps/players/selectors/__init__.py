"""Read-optimised player-domain queries (six-layer rule: no service imports)."""

from __future__ import annotations

from apps.players.selectors.players import (
    PLAYER_ORDERINGS,
    get_player,
    list_duplicate_candidates,
    list_players,
    resolve_player_redirect,
)

__all__ = [
    "PLAYER_ORDERINGS",
    "get_player",
    "list_duplicate_candidates",
    "list_players",
    "resolve_player_redirect",
]
