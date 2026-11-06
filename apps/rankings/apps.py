"""App configuration for the ``rankings`` module."""

from __future__ import annotations

from django.apps import AppConfig


class RankingsConfig(AppConfig):
    name = "apps.rankings"
    label = "rankings"
    verbose_name = "rankings"
    default_auto_field = "django.db.models.UUIDField"

    def ready(self) -> None:
        if getattr(self, "_ready_done", False):
            return
        # The fixture-backed adapter serves the recorded in-repo corpus (Group
        # 5); a licensed provider registers its own adapter in its app config.
        from apps.providers.registry import register_adapter
        from apps.rankings.adapters import FixtureRankingsAdapter

        register_adapter("fixture", FixtureRankingsAdapter)
        self._ready_done = True
