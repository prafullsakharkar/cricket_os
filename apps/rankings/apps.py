"""App configuration for the ``rankings`` module."""

from __future__ import annotations

from django.apps import AppConfig


class RankingsConfig(AppConfig):
    name = "apps.rankings"
    label = "rankings"
    verbose_name = "rankings"
    default_auto_field = "django.db.models.UUIDField"

    def ready(self) -> None:
        # No eager subscriptions yet: rankings import is job-driven (Group 5).
        pass
