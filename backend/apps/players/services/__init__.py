"""Player-domain write services (six-layer rule: selectors never import these)."""

from __future__ import annotations

from apps.players.services.identity_resolution import PlayerResolver
from apps.players.services.merge import merge_players, register_player_reference
from apps.players.services.players import mark_inactive, upsert_player, verify_mapping

__all__ = [
    "PlayerResolver",
    "mark_inactive",
    "merge_players",
    "register_player_reference",
    "upsert_player",
    "verify_mapping",
]
