"""App configuration for the teams module."""

from __future__ import annotations

from django.apps import AppConfig


class TeamsConfig(AppConfig):
    name = "apps.teams"
    label = "teams"
    verbose_name = "Teams"
    default_auto_field = "django.db.models.UUIDField"

    def ready(self) -> None:
        if getattr(self, "_ready_done", False):
            return
        from apps.players.services.merge import register_player_reference
        from apps.providers.services import register_entity_resolver
        from apps.teams.models import SquadMember, TeamMembership
        from apps.teams.services.teams import TeamResolver

        register_entity_resolver("team", TeamResolver())
        # Player references that merges must re-point loser -> winner.
        register_player_reference(TeamMembership, "player")
        register_player_reference(SquadMember, "player")
        self._ready_done = True
