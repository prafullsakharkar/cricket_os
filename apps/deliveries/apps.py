"""App configuration for the ``deliveries`` module."""

from __future__ import annotations

from django.apps import AppConfig


class DeliveriesConfig(AppConfig):
    name = "apps.deliveries"
    label = "deliveries"
    verbose_name = "deliveries"
    default_auto_field = "django.db.models.UUIDField"
