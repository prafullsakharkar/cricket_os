"""App configuration for the providers module."""

from __future__ import annotations

from django.apps import AppConfig


class ProvidersConfig(AppConfig):
    name = "apps.providers"
    label = "providers"
    verbose_name = "Providers"
    default_auto_field = "django.db.models.UUIDField"
