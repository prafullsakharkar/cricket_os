"""App configuration for the ``scorecards`` module."""

from __future__ import annotations

from django.apps import AppConfig


class ScorecardsConfig(AppConfig):
    name = "apps.scorecards"
    label = "scorecards"
    verbose_name = "scorecards"
    default_auto_field = "django.db.models.UUIDField"
