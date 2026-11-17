"""App configuration for the competitions module."""

from __future__ import annotations

from django.apps import AppConfig


class CompetitionsConfig(AppConfig):
    name = "apps.competitions"
    label = "competitions"
    verbose_name = "Competitions"
    default_auto_field = "django.db.models.UUIDField"

    def ready(self) -> None:
        if getattr(self, "_ready_done", False):
            return
        from apps.competitions.services.competitions import (
            CompetitionResolver,
            SeriesResolver,
        )
        from apps.providers.services import register_entity_resolver

        register_entity_resolver("competition", CompetitionResolver())
        register_entity_resolver("series", SeriesResolver())
        self._ready_done = True
