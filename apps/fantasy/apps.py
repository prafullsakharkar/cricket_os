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
        # recompute; player.merged → supersede + recompute) is added here in
        # Task 4 when apps/fantasy/services/recompute.py exists. Guarded so
        # repeated ready() calls (tests) do not double-register.
        if getattr(self, "_ready_done", False):
            return
        self._ready_done = True
