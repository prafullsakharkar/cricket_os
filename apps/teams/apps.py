"""App configuration for the teams module."""

from __future__ import annotations

from django.apps import AppConfig


class TeamsConfig(AppConfig):
    name = "apps.teams"
    label = "teams"
    verbose_name = "Teams"
    default_auto_field = "django.db.models.UUIDField"
