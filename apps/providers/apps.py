"""App configuration for the providers module."""

from __future__ import annotations

from django.apps import AppConfig


class ProvidersConfig(AppConfig):
    name = "apps.providers"
    label = "providers"
    verbose_name = "Providers"
    default_auto_field = "django.db.models.UUIDField"

    def ready(self) -> None:
        if getattr(self, "_ready_done", False):
            return
        # The internal fake provider (Phase 0 gate: no licensed adapter yet).
        from apps.providers.adapters.replay import ReplayAdapter
        from apps.providers.registry import register_adapter

        register_adapter("replay", ReplayAdapter)
        self._ready_done = True
