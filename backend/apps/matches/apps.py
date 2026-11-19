"""App configuration for the ``matches`` module."""

from __future__ import annotations

from django.apps import AppConfig


class MatchesConfig(AppConfig):
    name = "apps.matches"
    label = "matches"
    verbose_name = "matches"
    default_auto_field = "django.db.models.UUIDField"
