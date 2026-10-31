"""App configuration for the administration module."""

from __future__ import annotations

from django.apps import AppConfig


class AdministrationConfig(AppConfig):
    name = "apps.administration"
    label = "administration"
    verbose_name = "Administration"
    default_auto_field = "django.db.models.UUIDField"

    def ready(self) -> None:
        # Subscribe the audit handlers. Imported lazily so ready() never
        # triggers app-import cycles, and guarded so repeated ready() calls
        # (tests, reloads) do not double-subscribe.
        if getattr(self, "_ready_done", False):
            return
        from apps.administration import handlers
        from apps.core.events import (
            TOPIC_DUPLICATE_REVIEWED,
            TOPIC_PLAYER_MERGED,
            TOPIC_PROVIDER_MAP_VERIFIED,
            subscribe,
        )

        subscribe(TOPIC_PLAYER_MERGED, handlers.on_player_merged)
        subscribe(TOPIC_PROVIDER_MAP_VERIFIED, handlers.on_provider_map_verified)
        subscribe(TOPIC_DUPLICATE_REVIEWED, handlers.on_duplicate_reviewed)
        self._ready_done = True
