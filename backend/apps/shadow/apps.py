"""App configuration for the shadow module."""

from __future__ import annotations

from django.apps import AppConfig


class ShadowConfig(AppConfig):
    name = "apps.shadow"
    label = "shadow"
    verbose_name = "Shadow mode"
    default_auto_field = "django.db.models.UUIDField"
