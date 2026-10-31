"""App configuration for the venues module."""

from __future__ import annotations

from django.apps import AppConfig


class VenuesConfig(AppConfig):
    name = "apps.venues"
    label = "venues"
    verbose_name = "Venues"
    default_auto_field = "django.db.models.UUIDField"
