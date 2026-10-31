"""App configuration for the players module."""

from __future__ import annotations

from django.apps import AppConfig


class PlayersConfig(AppConfig):
    name = "apps.players"
    label = "players"
    verbose_name = "Players"
    default_auto_field = "django.db.models.UUIDField"
