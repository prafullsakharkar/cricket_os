"""App configuration for the ``synchronization`` module."""

from __future__ import annotations

from django.apps import AppConfig


class SynchronizationConfig(AppConfig):
    name = "apps.synchronization"
    label = "synchronization"
    verbose_name = "synchronization"
    default_auto_field = "django.db.models.UUIDField"
