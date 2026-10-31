"""App configuration for the players module."""

from __future__ import annotations

from django.apps import AppConfig


class PlayersConfig(AppConfig):
    name = "apps.players"
    label = "players"
    verbose_name = "Players"
    default_auto_field = "django.db.models.UUIDField"

    def ready(self) -> None:
        # Register the player identity resolver for the generic resolve().
        # Imported lazily so ready() never triggers app-import cycles, and
        # guarded so repeated ready() calls (tests) do not re-register.
        if getattr(self, "_ready_done", False):
            return
        from apps.players.services.identity_resolution import PlayerResolver
        from apps.providers.services import register_entity_resolver

        register_entity_resolver("player", PlayerResolver())
        self._ready_done = True
