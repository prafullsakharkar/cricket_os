"""App configuration for the ``statistics`` module."""

from __future__ import annotations

from django.apps import AppConfig


class StatisticsConfig(AppConfig):
    name = "apps.statistics"
    label = "statistics"
    verbose_name = "statistics"
    default_auto_field = "django.db.models.UUIDField"
