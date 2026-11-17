"""App configuration for the venues module."""

from __future__ import annotations

from django.apps import AppConfig


class VenuesConfig(AppConfig):
    name = "apps.venues"
    label = "venues"
    verbose_name = "Venues"
    default_auto_field = "django.db.models.UUIDField"

    def ready(self) -> None:
        if getattr(self, "_ready_done", False):
            return
        from apps.providers.services import register_entity_resolver
        from apps.venues.services.venues import VenueResolver

        register_entity_resolver("venue", VenueResolver())
        self._ready_done = True
