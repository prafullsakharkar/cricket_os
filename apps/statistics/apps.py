"""App configuration for the ``statistics`` module."""

from __future__ import annotations

from django.apps import AppConfig


class StatisticsConfig(AppConfig):
    name = "apps.statistics"
    label = "statistics"
    verbose_name = "statistics"
    default_auto_field = "django.db.models.UUIDField"

    def ready(self) -> None:
        # Tier-2 rebuild on match.completed / correction.applied: first the
        # per-match stats (build_match_stats), then the Phase F aggregates
        # (recompute pipeline). Guarded so repeated ready() calls (tests) do
        # not double-register.
        if getattr(self, "_ready_done", False):
            return
        from apps.statistics.services.recompute import register as recompute_register
        from apps.statistics.services.stats import register as stats_register

        stats_register()
        recompute_register()
        self._ready_done = True
