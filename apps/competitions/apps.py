"""App configuration for the competitions module."""

from __future__ import annotations

from django.apps import AppConfig


class CompetitionsConfig(AppConfig):
    name = "apps.competitions"
    label = "competitions"
    verbose_name = "Competitions"
    default_auto_field = "django.db.models.UUIDField"
