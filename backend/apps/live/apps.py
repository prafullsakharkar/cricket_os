"""App configuration for the live match pipeline (Phase D)."""

from __future__ import annotations

from django.apps import AppConfig


class LiveConfig(AppConfig):
    name = "apps.live"
    label = "live"
    verbose_name = "Live matches"
    default_auto_field = "django.db.models.UUIDField"
