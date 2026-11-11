"""App configuration for the ``fantasy`` module (Phase H)."""

from __future__ import annotations

from django.apps import AppConfig


class FantasyConfig(AppConfig):
    name = "apps.fantasy"
    label = "fantasy"
    verbose_name = "fantasy"
    default_auto_field = "django.db.models.UUIDField"

    def ready(self) -> None:
        # Tier-2 pipeline wiring (match.completed / correction.applied →
        # recompute; player.merged → recompute the winner). Registered after
        # the statistics module's handlers (INSTALLED_APPS order) so
        # per-match stats and aggregates exist before fantasy computes.
        if getattr(self, "_ready_done", False):
            return
        from apps.fantasy.services.recompute import register as recompute_register

        recompute_register()
        self._ready_done = True
